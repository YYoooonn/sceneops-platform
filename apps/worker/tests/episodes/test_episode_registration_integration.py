"""REGISTER_EPISODES against real PostgreSQL and real MinIO (ADR-015 §6.4,
MS-7): the durability and integrity gate checked against the objects in
MinIO, additive revisions, concurrent convergence, complete revision pins,
and no DatasetVersion read or written.

Requires SCENEOPS_DATABASE_URL (a database migrated to head) and a
reachable MinIO; skips otherwise. Runs in the disposable database and
bucket of `make test-integration`; every test uses its own ids, including
its topics, so no two tests share a payload artifact.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.episodes.schemas import episode_id_for
from sceneops_core.episodes.testing import action, episode_manifest, observation
from sceneops_core.provenance import RevisionDecision, manifest_artifact_id_for
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_db.models.datasets import DatasetVersionMemberModel
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.episodes.registration import (
    EpisodeManifestRejectedError,
    EpisodeRegistrationConflictError,
    register_episodes,
)

RECORDING_CHECKSUM = "sha256:" + "1" * 64


class _Env:
    def __init__(self, settings, prefix: str) -> None:
        self.settings = settings
        self.run_id = f"run-{prefix}"
        self.camera = f"/{prefix}/camera"
        self.cmd = f"/{prefix}/cmd_vel"

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    def manifest(self, unit_key="recording", *, offset=0, build_config=None):
        return episode_manifest(
            [
                observation(self.camera, 0),
                observation(self.camera, 100),
                action(self.cmd, 50 + offset, linear=1.0),
            ],
            robot_run_id=self.run_id,
            unit_key=unit_key,
            recording_checksum=RECORDING_CHECKSUM,
            window=(0, 1_000),
            build_config=build_config,
        )

    def payload_uri(self, artifact_id: str) -> str:
        return f"{self.settings.artifact_root_uri}/payloads/{artifact_id}"

    async def publish(self, manifest) -> str:
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            for occurrence in manifest.occurrences():
                ref = occurrence.payload
                if ref is None or await ctx.artifact_record_store.get(ref.artifact_id):
                    continue
                rank = int(occurrence.occurrence_id.rsplit("-", 1)[1])
                await ctx.artifact_store.write_bytes(
                    self.payload_uri(ref.artifact_id),
                    f"payload:{occurrence.topic}:{rank}".encode(),
                )
                await ctx.artifact_record_store.create(
                    artifact_id=ref.artifact_id,
                    ref=ArtifactRef(
                        kind=ArtifactKind.OBSERVATION_PAYLOAD,
                        uri=self.payload_uri(ref.artifact_id),
                        media_type=ref.media_type,
                        size_bytes=ref.size_bytes,
                        checksum=ref.checksum,
                    ),
                )
            episode_id = episode_id_for(manifest.lineage.source)
            published = await ctx.episode_artifact_store.publish_canonical_manifest(
                episode_id=episode_id, manifest=manifest
            )
            artifact_id = manifest_artifact_id_for(
                unit_id=episode_id, manifest_checksum=published.checksum
            )
            if await ctx.artifact_record_store.get(artifact_id) is None:
                await ctx.artifact_record_store.create(
                    artifact_id=artifact_id,
                    ref=ArtifactRef(
                        kind=ArtifactKind.EPISODE_MANIFEST,
                        uri=published.uri,
                        media_type="application/json",
                        size_bytes=published.size_bytes,
                        checksum=published.checksum,
                    ),
                    owner_type=ArtifactOwnerType.EPISODE,
                    owner_id=episode_id,
                )
            await session.commit()
            return artifact_id

    async def seed_robot_run(self) -> None:
        robot_id = f"robot-{self.run_id}"
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            await ctx.robot_store.create_robot_if_absent(RobotRecord(robot_id=robot_id))
            for artifact_id, kind in (
                (
                    robot_run_recording_artifact_id(self.run_id),
                    ArtifactKind.ROBOT_RUN_RECORDING,
                ),
                (
                    robot_run_manifest_artifact_id(self.run_id),
                    ArtifactKind.ROBOT_RUN_MANIFEST,
                ),
            ):
                await ctx.artifact_record_store.create(
                    artifact_id=artifact_id,
                    ref=ArtifactRef(
                        kind=kind,
                        uri=f"s3://test/{artifact_id}",
                        size_bytes=1,
                        checksum=RECORDING_CHECKSUM,
                    ),
                    owner_type=ArtifactOwnerType.ROBOT_RUN,
                    owner_id=self.run_id,
                )
            await ctx.robot_store.create_run(
                RobotRunRecord(
                    run_id=self.run_id,
                    robot_id=robot_id,
                    started_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
                    recording_format="mcap",
                    source_clock="mcap_log_time",
                    recording_artifact_id=robot_run_recording_artifact_id(self.run_id),
                    manifest_artifact_id=robot_run_manifest_artifact_id(self.run_id),
                    manifest_checksum=RECORDING_CHECKSUM,
                )
            )
            await session.commit()

    async def register(self, artifact_ids):
        async with get_async_sessionmaker()() as session:
            return await register_episodes(
                context=self.context(session), manifest_artifact_ids=artifact_ids
            )

    async def revisions(self) -> dict:
        async with get_async_sessionmaker()() as session:
            store = self.context(session).episode_store
            return {
                (r.episode_id, r.manifest_checksum): r
                for unit in await store.list(robot_run_id=self.run_id)
                for r in await store.list_revisions(unit.episode_id)
            }


@pytest.fixture()
async def env(_fresh_database_connection, _minio_reachable, worker_settings, unique_id):
    from sceneops_worker.core import dependencies as dependencies_module

    dependencies_module._artifact_store = None
    environment = _Env(worker_settings, unique_id("episodes"))
    await environment.seed_robot_run()
    yield environment
    dependencies_module._artifact_store = None


async def test_registration_lifecycle_on_postgres(env):
    a = await env.publish(env.manifest("a"))
    b = await env.publish(env.manifest("b"))

    created = await env.register([b, a])
    committed = await env.revisions()
    assert [p.unit_id for p in created.pins] == sorted(e for e, _ in committed)
    assert {p.domain for p in created.pins} == {"episode"}
    assert {p.manifest_artifact_id for p in created.pins} == {a, b}
    assert {r.decision for r in created.revisions} == {RevisionDecision.NEW_UNIT}

    retry = await env.register([a, b])
    assert retry.pins == created.pins
    assert await env.revisions() == committed

    rebuilt = await env.publish(env.manifest("a", build_config={"other": True}))
    (added,) = (await env.register([rebuilt])).revisions
    assert added.decision == RevisionDecision.NEW_REVISION
    assert len(await env.revisions()) == 3

    async with get_async_sessionmaker()() as session:
        members = (
            await session.execute(
                select(DatasetVersionMemberModel).where(
                    DatasetVersionMemberModel.unit_id.in_(
                        [p.unit_id for p in created.pins]
                    )
                )
            )
        ).all()
    assert members == []


async def test_a_drifting_producer_is_refused(env):
    await env.register([await env.publish(env.manifest("a"))])
    before = await env.revisions()
    drifted = await env.publish(env.manifest("a", offset=1))
    with pytest.raises(EpisodeRegistrationConflictError, match="not deterministic"):
        await env.register([drifted])
    assert await env.revisions() == before


async def test_concurrent_identical_registrations_converge(env):
    artifacts = [await env.publish(env.manifest(key)) for key in ("a", "b", "c")]
    for _ in range(2):
        results = await asyncio.gather(
            env.register(artifacts), env.register(list(reversed(artifacts)))
        )
        assert results[0].pins == results[1].pins
    assert len(await env.revisions()) == 3


async def test_a_payload_object_missing_from_minio_is_rejected(env):
    manifest = env.manifest("a")
    artifact_id = await env.publish(manifest)
    payload = manifest.observations[0].payload
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_store.delete_prefix(
            env.payload_uri(payload.artifact_id)
        )
    with pytest.raises(EpisodeManifestRejectedError, match="has no object"):
        await env.register([artifact_id])
    assert await env.revisions() == {}


async def test_a_payload_object_of_another_size_is_rejected(env):
    manifest = env.manifest("a")
    artifact_id = await env.publish(manifest)
    payload = manifest.observations[1].payload
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_store.write_bytes(
            env.payload_uri(payload.artifact_id), b"x"
        )
    with pytest.raises(EpisodeManifestRejectedError, match="1 bytes, recorded"):
        await env.register([artifact_id])
    assert await env.revisions() == {}


async def test_manifest_bytes_tampered_in_object_storage_are_rejected(env):
    artifact_id = await env.publish(env.manifest("a"))
    async with get_async_sessionmaker()() as session:
        ctx = env.context(session)
        artifact = await ctx.artifact_record_store.get(artifact_id)
        data = await ctx.artifact_store.read_bytes(artifact.uri)
        await ctx.artifact_store.write_bytes(artifact.uri, data + b" ")
    with pytest.raises(EpisodeManifestRejectedError, match="bytes, pinned"):
        await env.register([artifact_id])
    assert await env.revisions() == {}
