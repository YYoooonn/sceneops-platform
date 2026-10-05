"""REGISTER_SCENES against real PostgreSQL and real MinIO: canonical
manifests published to S3 at checksum-qualified keys, registration in one
transaction under the DatasetVersion row lock, concurrent registrations
into one DatasetVersion, and all-or-nothing behavior (ADR-007 §17-§18,
I-1, I-3, I-10-I-12, I-24).

Requires SCENEOPS_DATABASE_URL (a database migrated to head) and a
reachable MinIO; skips otherwise. Every test commits rows under unique ids
and removes them afterwards.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import (
    build_scene_manifest,
    payload_bytes,
    recording_source,
)
from sceneops_db.models.artifacts import ArtifactModel
from sceneops_db.models.datasets import DatasetModel, DatasetVersionModel
from sceneops_db.models.robots import RobotModel, RobotRunModel
from sceneops_db.models.scenes import SceneModel
from sceneops_db.postgres.datasets import PostgresDatasetVersionRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    SceneRegistrationConflictError,
    register_scenes,
)

pytestmark = pytest.mark.usefixtures("cleanup_minio_prefix")

RECORDING_CHECKSUM = "sha256:" + "1" * 64


class _Env:
    def __init__(self, settings, dataset_id: str) -> None:
        self.settings = settings
        self.dataset_id = dataset_id
        self.run_ids: list[str] = []
        self.robot_ids: list[str] = []
        # The RobotRun every default manifest of this environment is built from.
        self.run_id = f"run-{dataset_id}"

    def source(self, unit_key: str = "segment-000000", run_id: str | None = None):
        return recording_source(
            robot_run_id=run_id or self.run_id,
            unit_key=unit_key,
            recording_checksum=RECORDING_CHECKSUM,
        )

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    async def seed_dataset_version(self, version: str = "v1") -> None:
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            if await ctx.dataset_store.get_dataset(self.dataset_id) is None:
                await ctx.dataset_store.create_dataset(
                    DatasetRecord(dataset_id=self.dataset_id)
                )
            await ctx.dataset_store.create_version(
                DatasetVersionRecord(dataset_id=self.dataset_id, version=version)
            )
            await session.commit()

    def manifest(self, **kwargs):
        kwargs.setdefault("payload_namespace", f"art-payload-{self.dataset_id}")
        kwargs.setdefault("source", self.source())
        return build_scene_manifest(**kwargs)

    async def register_payloads(self, ctx, manifest) -> None:
        """Producer side: payload bytes to MinIO plus their
        OBSERVATION_PAYLOAD ArtifactRecords (idempotent per artifact id)."""
        for observation in manifest.observations:
            ref = observation.payload
            if await ctx.artifact_record_store.get(ref.artifact_id) is not None:
                continue
            uri = f"{self.settings.artifact_root_uri}/payloads/{ref.artifact_id}"
            await ctx.artifact_store.write_bytes(
                uri, payload_bytes(observation.observation_id)
            )
            await ctx.artifact_record_store.create(
                artifact_id=ref.artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.OBSERVATION_PAYLOAD,
                    uri=uri,
                    media_type=ref.media_type,
                    size_bytes=ref.size_bytes,
                    checksum=ref.checksum,
                ),
                dataset_id=self.dataset_id,
            )

    async def publish(
        self, manifest, version: str = "v1", *, with_payloads: bool = True
    ) -> str:
        """Producer side: payload artifacts, then write-once manifest bytes
        to MinIO plus the SCENE_MANIFEST ArtifactRecord, committed."""
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            if with_payloads:
                await self.register_payloads(ctx, manifest)
            scene_id = scene_id_for(
                dataset_id=self.dataset_id,
                dataset_version=version,
                source=manifest.lineage.source,
            )
            published = await ctx.scene_artifact_store.publish_canonical_manifest(
                dataset_id=self.dataset_id,
                dataset_version=version,
                scene_id=scene_id,
                manifest=manifest,
            )
            artifact_id = f"art-scene-{published.checksum[7:39]}"
            if await ctx.artifact_record_store.get(artifact_id) is None:
                await ctx.artifact_record_store.create(
                    artifact_id=artifact_id,
                    ref=ArtifactRef(
                        kind=ArtifactKind.SCENE_MANIFEST,
                        uri=published.uri,
                        media_type="application/json",
                        size_bytes=published.size_bytes,
                        checksum=published.checksum,
                    ),
                    owner_type=ArtifactOwnerType.SCENE,
                    owner_id=scene_id,
                    dataset_id=self.dataset_id,
                    dataset_version=version,
                    scene_id=scene_id,
                )
            await session.commit()
            return artifact_id

    async def seed_robot_run(self, run_id: str) -> None:
        robot_id = f"robot-{run_id}"
        self.run_ids.append(run_id)
        self.robot_ids.append(robot_id)
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            await ctx.robot_store.create_robot_if_absent(RobotRecord(robot_id=robot_id))
            for artifact_id, kind, checksum in (
                (
                    robot_run_recording_artifact_id(run_id),
                    ArtifactKind.ROBOT_RUN_RECORDING,
                    RECORDING_CHECKSUM,
                ),
                (
                    robot_run_manifest_artifact_id(run_id),
                    ArtifactKind.ROBOT_RUN_MANIFEST,
                    "sha256:" + "0" * 64,
                ),
            ):
                await ctx.artifact_record_store.create(
                    artifact_id=artifact_id,
                    ref=ArtifactRef(
                        kind=kind,
                        uri=f"s3://test/{artifact_id}",
                        size_bytes=1,
                        checksum=checksum,
                    ),
                    owner_type=ArtifactOwnerType.ROBOT_RUN,
                    owner_id=run_id,
                )
            await ctx.robot_store.create_run(
                RobotRunRecord(
                    run_id=run_id,
                    robot_id=robot_id,
                    started_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
                    recording_format="mcap",
                    source_clock="mcap_log_time",
                    recording_artifact_id=robot_run_recording_artifact_id(run_id),
                    manifest_artifact_id=robot_run_manifest_artifact_id(run_id),
                    manifest_checksum="sha256:" + "0" * 64,
                )
            )
            await session.commit()

    async def register(
        self, artifact_ids, *, version: str = "v1", replace: bool = False
    ):
        async with get_async_sessionmaker()() as session:
            return await register_scenes(
                context=self.context(session),
                dataset_id=self.dataset_id,
                dataset_version=version,
                manifest_artifact_ids=artifact_ids,
                replace=replace,
            )

    async def committed(self, version: str = "v1"):
        async with get_async_sessionmaker()() as session:
            scenes = await PostgresSceneRepository(session).list(
                dataset_id=self.dataset_id, dataset_version=version
            )
            dv = await PostgresDatasetVersionRepository(session).get(
                dataset_id=self.dataset_id, version=version
            )
        return {s.scene_id: s for s in scenes}, dv

    async def cleanup(self) -> None:
        async with get_async_sessionmaker()() as session:
            await session.execute(
                delete(SceneModel).where(SceneModel.dataset_id == self.dataset_id)
            )
            await session.execute(
                delete(RobotRunModel).where(RobotRunModel.run_id.in_(self.run_ids))
            )
            await session.execute(
                delete(ArtifactModel).where(
                    (ArtifactModel.dataset_id == self.dataset_id)
                    | ArtifactModel.owner_id.in_(self.run_ids)
                )
            )
            await session.execute(
                delete(RobotModel).where(RobotModel.robot_id.in_(self.robot_ids))
            )
            await session.execute(
                delete(DatasetVersionModel).where(
                    DatasetVersionModel.dataset_id == self.dataset_id
                )
            )
            await session.execute(
                delete(DatasetModel).where(DatasetModel.dataset_id == self.dataset_id)
            )
            await session.commit()


@pytest.fixture()
async def env(_fresh_database_connection, _minio_reachable, worker_settings, unique_id):
    from sceneops_worker.core import dependencies as dependencies_module

    dependencies_module._artifact_store = None
    environment = _Env(worker_settings, unique_id("ds-scenes"))
    await environment.seed_dataset_version()
    await environment.seed_robot_run(environment.run_id)
    yield environment
    await environment.cleanup()
    dependencies_module._artifact_store = None


async def test_recording_scope_lifecycle_on_postgres(env):
    first = await env.publish(env.manifest(source=env.source("a")))
    other = await env.publish(
        env.manifest(source=env.source("b"), keyframe_timestamps_ns=(1_000,))
    )

    created = await env.register([first, other])
    scenes, dv = await env.committed()
    assert set(scenes) == set(created.created_scene_ids)
    assert {s.robot_run_id for s in scenes.values()} == {env.run_id}
    assert dv.scene.scene_count == 2
    assert dv.scene.keyframe_count == 3
    assert dv.scene.observation_count == 7
    assert dv.scene.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]

    # Identical retry converges.
    retry = await env.register([first, other])
    assert sorted(retry.unchanged_scene_ids) == sorted(created.created_scene_ids)

    # A different build of the recording conflicts without replace.
    rebuilt = await env.publish(
        env.manifest(source=env.source("a"), build_config={"channels": []})
    )
    with pytest.raises(SceneRegistrationConflictError):
        await env.register([rebuilt])
    assert (await env.committed())[0] == scenes

    # With replace the scope becomes exactly the new set: "a" is repointed
    # in place, "b" is removed, the old revision stays retrievable.
    replaced = await env.register([rebuilt], replace=True)
    scenes_after, dv_after = await env.committed()
    scene_a = replaced.replaced_scene_ids[0]
    assert (
        replaced.removed_scene_ids and replaced.removed_scene_ids[0] not in scenes_after
    )
    assert scenes_after[scene_a].manifest_artifact_id == rebuilt
    assert scenes_after[scene_a].updated_at >= scenes[scene_a].updated_at
    assert dv_after.scene.scene_count == 1
    async with get_async_sessionmaker()() as session:
        ctx = env.context(session)
        assert await ctx.artifact_record_store.get(first) is not None


async def test_registration_is_atomic_on_postgres(env):
    current = [
        await env.publish(env.manifest(source=env.source(key))) for key in ("a", "b")
    ]
    await env.register(current)
    before, dv_before = await env.committed()

    rebuilt = [
        await env.publish(env.manifest(source=env.source(key), build_config={"x": 1}))
        for key in ("b", "c")
    ]
    with pytest.raises(SceneRegistrationConflictError):
        await env.register(rebuilt)

    after, dv_after = await env.committed()
    assert after == before
    assert dv_after.scene == dv_before.scene


async def test_concurrent_registrations_serialize_on_the_dataset_version(
    env, unique_id
):
    """Two RobotRuns' scopes registered concurrently into one DatasetVersion:
    the row lock serializes their recompute-then-write, so the summary
    counts both."""
    other_run = unique_id("run")
    await env.seed_robot_run(other_run)
    a = await env.publish(env.manifest(source=env.source("a")))
    b = await env.publish(env.manifest(source=env.source("b", run_id=other_run)))

    await asyncio.gather(env.register([a]), env.register([b]))

    scenes, dv = await env.committed()
    assert len(scenes) == 2
    assert dv.scene.scene_count == 2


async def test_concurrent_conflicting_revisions_have_one_winner(env):
    a1 = await env.publish(env.manifest(source=env.source("a")))
    a2 = await env.publish(env.manifest(source=env.source("a"), build_config={"x": 2}))

    results = await asyncio.gather(
        env.register([a1]), env.register([a2]), return_exceptions=True
    )

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1
    assert isinstance(errors[0], SceneRegistrationConflictError)
    scenes, dv = await env.committed()
    assert len(scenes) == 1 and dv.scene.scene_count == 1
    winner = next(r for r in results if not isinstance(r, Exception))
    assert (
        next(iter(scenes.values())).manifest_artifact_id
        == winner.scenes[0].manifest_artifact_id
    )


async def test_recording_scope_replacement_on_postgres(env, unique_id):
    run_id = unique_id("run")
    await env.seed_robot_run(run_id)

    def segment(key, start, end):
        return recording_source(
            robot_run_id=run_id,
            unit_key=key,
            start_timestamp_ns=start,
            end_timestamp_ns=end,
            recording_checksum=RECORDING_CHECKSUM,
        )

    async def build(segments, config):
        return [
            await env.publish(
                env.manifest(
                    source=s,
                    build_config=config,
                    keyframe_timestamps_ns=(s.start_timestamp_ns + 1,),
                )
            )
            for s in segments
        ]

    first = await build(
        [segment("seg-0", 0, 100), segment("seg-1", 100, 200)], {"v": 1}
    )
    await env.register(first)
    second = await build(
        [segment("seg-0", 0, 150), segment("seg-9", 150, 200)], {"v": 2}
    )
    result = await env.register(second, replace=True)

    scenes, dv = await env.committed()
    assert (
        len(result.removed_scene_ids) == 1 and result.removed_scene_ids[0] not in scenes
    )
    assert {s.producer_fingerprint for s in scenes.values()} == {
        s.producer_fingerprint for s in result.scenes
    }
    assert len({s.producer_fingerprint for s in scenes.values()}) == 1
    assert dv.scene.scene_count == 2


async def test_manifest_bytes_tampered_in_object_storage_are_rejected(env):
    artifact_id = await env.publish(env.manifest())
    async with get_async_sessionmaker()() as session:
        ctx = env.context(session)
        artifact = await ctx.artifact_record_store.get(artifact_id)
        data = await ctx.artifact_store.read_bytes(artifact.uri)
        await ctx.artifact_store.write_bytes(
            artifact.uri, data.replace(b"LIDAR_TOP", b"LIDAR_TOQ")
        )

    with pytest.raises(SceneManifestRejectedError, match="checksum"):
        await env.register([artifact_id])
    assert (await env.committed())[0] == {}


async def test_payload_refs_are_checked_against_artifact_records_on_postgres(env):
    unregistered = await env.publish(
        env.manifest(source=env.source("a")),
        with_payloads=False,
    )
    with pytest.raises(SceneManifestRejectedError, match="not registered"):
        await env.register([unregistered])

    # A payload ArtifactRecord whose integrity metadata differs from the
    # reference (here: same id, different bytes) is not the referenced payload.
    manifest = env.manifest(source=env.source("b"))
    payload = manifest.observations[0].payload
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_record_store.create(
            artifact_id=payload.artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.OBSERVATION_PAYLOAD,
                uri=f"{env.settings.artifact_root_uri}/payloads/other",
                media_type=payload.media_type,
                size_bytes=payload.size_bytes,
                checksum="sha256:" + "0" * 64,
            ),
            dataset_id=env.dataset_id,
        )
        await session.commit()
    mismatched = await env.publish(manifest)
    with pytest.raises(SceneManifestRejectedError, match="checksum"):
        await env.register([mismatched])
    assert (await env.committed())[0] == {}


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("kind", ArtifactKind.SCENE_MANIFEST.value, "is a"),
        ("size_bytes", 1, "size_bytes"),
        ("media_type", "image/png", "media_type"),
    ],
)
async def test_payload_artifact_record_metadata_is_verified_on_postgres(
    env, column, value, message
):
    """Each PayloadRef must match its ArtifactRecord as stored in PostgreSQL
    (kind round-trips as a plain string); a mismatch on any one payload
    rejects the whole registration and commits nothing."""
    manifest = env.manifest(source=env.source("a"))
    artifact_id = await env.publish(manifest)
    payload_id = manifest.observations[0].payload.artifact_id
    async with get_async_sessionmaker()() as session:
        await session.execute(
            update(ArtifactModel)
            .where(ArtifactModel.artifact_id == payload_id)
            .values({column: value})
        )
        await session.commit()
    _, dv_before = await env.committed()

    with pytest.raises(SceneManifestRejectedError, match=message):
        await env.register([artifact_id])
    scenes, dv_after = await env.committed()
    assert scenes == {}
    assert dv_after.scene == dv_before.scene
