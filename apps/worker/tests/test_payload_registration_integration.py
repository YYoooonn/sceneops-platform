"""The payload registration gate (ADR-017) against real
PostgreSQL and real MinIO: recording payload records in the ``artifacts``
table, SourceContents registered by REGISTER_SOURCE_SNAPSHOT, and objects in
the S3 bucket.

``artifacts.uri`` has no unique constraint, so two records may name the same
object; the gate finds a recording payload's record by the artifact id of the
URI's payload key and never by its physical URI, which this proves with such
a pair. The gate reads only: no unit, revision, DatasetVersion member,
ArtifactRecord or object is added.

Requires the disposable database and bucket of `make test-integration`;
skips (and so fails there) otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRef,
    PayloadRef,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.episodes import testing as episode_testing
from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig
from sceneops_core.provenance import PinnedContent
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_core.scenes import testing as scene_testing
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_db.session import get_async_sessionmaker
from sceneops_episodes.lerobot_builder import plan_lerobot_episodes
from sceneops_recording.observations.store import ObservationPayloadStore
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes
from sceneops_sources import (
    LocalContentFiles,
    content_file_uri,
    lerobot_synthetic,
    nuscenes_synthetic,
    publish_snapshot,
)
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.payload_registration import (
    PayloadRegistrationError,
    verify_content_payload_refs,
    verify_recording_payload_refs,
)
from sceneops_worker.sources.content import resolve_registered_content
from sceneops_worker.sources.registration import register_source_snapshot

TABLES = (
    "artifacts",
    "scenes",
    "scene_revisions",
    "episodes",
    "episode_revisions",
    "dataset_version_members",
)


def _run(run_id: str) -> RobotRunRecord:
    return RobotRunRecord(
        run_id=run_id,
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=f"art-recording-{run_id}",
        manifest_artifact_id=f"art-robotrunmanifest-{run_id}",
        manifest_checksum="sha256:" + "1" * 64,
    )


class _Env:
    def __init__(self, settings, prefix: str, tmp_path: Path) -> None:
        self.settings = settings
        self.prefix = prefix
        self.tmp_path = tmp_path
        self.store = S3ArtifactStore(settings=settings.artifact)
        self.payloads = ObservationPayloadStore(
            artifact_store=self.store,
            payload_root_uri=settings.artifact.observation_payload_root_uri,
        )

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    async def publish_payload(
        self, artifact_id: str, data: bytes, media_type: str, *, run_id: str
    ) -> PayloadRef:
        """Producer side: the object at its payload key and its
        OBSERVATION_PAYLOAD record, as the recording builders leave them."""
        uri, _ = await self.payloads.publish(
            robot_run_id=run_id,
            artifact_id=artifact_id,
            data=data,
            checksum=sha256_checksum(data),
        )
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            await context.artifact_record_store.create(
                artifact_id=artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.OBSERVATION_PAYLOAD,
                    uri=uri,
                    media_type=media_type,
                    size_bytes=len(data),
                    checksum=sha256_checksum(data),
                ),
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=run_id,
            )
            await context.commit()
        return PayloadRef(
            uri=format_logical_uri(
                LogicalNamespace.OBSERVATION_PAYLOADS, f"{run_id}/{artifact_id}"
            ),
            checksum=sha256_checksum(data),
            size_bytes=len(data),
            media_type=media_type,
        )

    async def content(self, tree: Path, fmt: str, include):
        """Publish ``tree`` to MinIO and register it; returns
        (RegisteredContent, PublishedSnapshot)."""
        published = await publish_snapshot(
            store=self.store,
            sources_root=self.settings.artifact.source_root_uri,
            source_dir=tree,
            declaration=SnapshotDeclaration(
                format=fmt,
                upstream=f"test/{self.prefix}",
                upstream_revision=tree.name,
                selection=SnapshotSelection(include=tuple(include)),
            ),
            annotations={"license": "test"},
        )
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            await register_source_snapshot(
                context=context, descriptor_uri=published.descriptor_uri
            )
        async with get_async_sessionmaker()() as session:
            content = await resolve_registered_content(
                self.context(session), published.content_id
            )
        return content, published

    async def verify(self, verifier, owner, refs):
        async with get_async_sessionmaker()() as session:
            return await verifier(self.context(session), owner, refs)

    async def state(self) -> tuple[dict[str, int], list[tuple[str, int]]]:
        """Row counts of every table the gate must not write, and every object
        under this test's root."""
        async with get_async_sessionmaker()() as session:
            rows = {
                table: (
                    await session.execute(text(f"SELECT count(*) FROM {table}"))
                ).scalar_one()
                for table in TABLES
            }
        objects = await self.store.list_objects(self.settings.artifact_root_uri)
        return rows, sorted((o.uri, o.size_bytes) for o in objects)


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    yield _Env(worker_settings, unique_id("payloadgate"), tmp_path)
    dependencies_module._artifact_store = None


def _artifact_id(ref: PayloadRef) -> str:
    """The payload artifact id a recording payload key names."""
    return ref.logical_uri().key.rpartition("/")[2]


async def test_recording_scene_and_episode_payloads_validate_against_their_records(env):
    run_id = f"run-{env.prefix}"
    scene = scene_testing.build_scene_manifest(
        source=scene_testing.recording_source(robot_run_id=run_id),
        payload_namespace=f"art-payload-{env.prefix}",
    )
    for observation in scene.observations:
        await env.publish_payload(
            _artifact_id(observation.payload),
            scene_testing.payload_bytes(observation.observation_id),
            observation.payload.media_type,
            run_id=run_id,
        )
    episode = episode_testing.episode_manifest(
        [
            episode_testing.observation(f"/cam/{env.prefix}", 0),
            episode_testing.state("/odom", 1_000_000_000, x=1.0),
        ],
        robot_run_id=run_id,
        window=(0, 2_000_000_000),
    )
    for occurrence in episode.occurrences():
        if occurrence.payload is not None:
            rank = int(occurrence.occurrence_id.rsplit("-", 1)[1])
            await env.publish_payload(
                _artifact_id(occurrence.payload),
                episode_testing.payload_bytes(occurrence.topic, rank),
                occurrence.payload.media_type,
                run_id=run_id,
            )
    refs = [o.payload for o in scene.observations] + [
        o.payload for o in episode.occurrences() if o.payload is not None
    ]

    before = await env.state()
    verified = await env.verify(verify_recording_payload_refs, _run(run_id), refs * 2)
    assert await env.state() == before
    assert sorted(verified) == sorted({r.uri for r in refs})
    root = env.settings.artifact.observation_payload_root_uri
    assert all(p.startswith(f"{root}/{run_id}/") for p in verified.values())

    # The same references are not payloads of any other RobotRun.
    with pytest.raises(PayloadRegistrationError, match="names a payload of RobotRun"):
        await env.verify(verify_recording_payload_refs, _run(f"{run_id}-x"), refs)


async def test_two_records_of_one_object_cannot_borrow_its_ownership(env):
    """``artifacts.uri`` is not unique: a second record of another RobotRun
    naming the same object is accepted by the database, and the gate still
    attributes the object to neither run through it."""
    run_id, other_run = f"run-{env.prefix}", f"run-{env.prefix}-other"
    data = b"\xff\xd8\xff payload"
    ref = await env.publish_payload(
        f"payload-{env.prefix}", data, "image/jpeg", run_id=run_id
    )
    async with get_async_sessionmaker()() as session:
        real = await env.context(session).artifact_record_store.get(
            f"payload-{env.prefix}"
        )
    spoof_id = f"payload-{env.prefix}-spoof"
    async with get_async_sessionmaker()() as session:
        context = env.context(session)
        await context.artifact_record_store.create(
            artifact_id=spoof_id,
            ref=ArtifactRef(
                kind=ArtifactKind.OBSERVATION_PAYLOAD,
                uri=real.uri,
                media_type="image/jpeg",
                size_bytes=len(data),
                checksum=sha256_checksum(data),
            ),
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=other_run,
        )
        await context.commit()
    async with get_async_sessionmaker()() as session:
        (count,) = (
            await session.execute(
                text("SELECT count(*) FROM artifacts WHERE uri = :u"), {"u": real.uri}
            )
        ).one()
    assert count == 2

    def at(run: str, artifact_id: str) -> PayloadRef:
        return ref.model_copy(
            update={
                "uri": format_logical_uri(
                    LogicalNamespace.OBSERVATION_PAYLOADS, f"{run}/{artifact_id}"
                )
            }
        )

    # The real record still backs its own reference.
    await env.verify(verify_recording_payload_refs, _run(run_id), [ref])
    # The other run's record is found by its id and names run_id's object.
    with pytest.raises(PayloadRegistrationError, match=f"is stored at {real.uri}"):
        await env.verify(
            verify_recording_payload_refs, _run(other_run), [at(other_run, spoof_id)]
        )
    with pytest.raises(PayloadRegistrationError, match="not by RobotRun"):
        await env.verify(
            verify_recording_payload_refs, _run(run_id), [at(run_id, spoof_id)]
        )


async def test_nuscenes_scene_payloads_validate_against_their_own_content(env):
    tree = nuscenes_synthetic.write_nuscenes_tree(
        env.tmp_path / "nuscenes-a", scenes=2, samples=2, sweeps=1
    )
    (tree / "LICENSE").write_text(f"{env.prefix} a\n")
    content, published = await env.content(tree, "nuscenes", nuscenes_synthetic.INCLUDE)
    plan = plan_nuscenes_scenes(
        pinned=PinnedContent(inventory=published.inventory),
        files=LocalContentFiles(tree, published.inventory),
        config=NuScenesSceneBuildConfig(channels=sorted(nuscenes_synthetic.CHANNELS)),
    )
    refs = [o.payload for p in plan.scenes for o in p.manifest.observations]
    files_key = f"contents/{content.record.content_id}/files/"

    before = await env.state()
    verified = await env.verify(verify_content_payload_refs, content, refs)
    assert await env.state() == before
    assert verified == {
        ref.uri: content_file_uri(
            content.inventory_uri, ref.logical_uri().key.removeprefix(files_key)
        )
        for ref in refs
    }

    # Another content holding identical files does not own these references.
    other_tree = nuscenes_synthetic.write_nuscenes_tree(
        env.tmp_path / "nuscenes-b", scenes=2, samples=2, sweeps=1
    )
    (other_tree / "LICENSE").write_text(f"{env.prefix} b\n")
    other, _ = await env.content(other_tree, "nuscenes", nuscenes_synthetic.INCLUDE)
    assert other.record.content_id != content.record.content_id
    with pytest.raises(PayloadRegistrationError, match="does not name a file of"):
        await env.verify(verify_content_payload_refs, other, refs)


async def test_lerobot_episode_frame_selectors_validate_as_stored_references(env):
    tree = lerobot_synthetic.write_lerobot_tree(env.tmp_path / "lerobot")
    (tree / "README.md").write_text(f"{env.prefix}\n")
    content, published = await env.content(tree, "lerobot", lerobot_synthetic.INCLUDE)
    plan = plan_lerobot_episodes(
        pinned=PinnedContent(inventory=published.inventory),
        files=LocalContentFiles(tree, published.inventory),
        config=LeRobotEpisodeBuildConfig(
            features=[
                {"feature": "observation.image", "role": "observation"},
                {"feature": "observation.state", "role": "state"},
                {"feature": "action", "role": "action"},
            ]
        ),
    )
    refs = [
        o.payload
        for p in plan.episodes
        for o in p.manifest.occurrences()
        if o.payload is not None
    ]
    assert refs and all(r.selector is not None for r in refs)

    before = await env.state()
    verified = await env.verify(verify_content_payload_refs, content, refs)
    assert await env.state() == before
    assert verified == {
        refs[0].uri: content_file_uri(
            content.inventory_uri, lerobot_synthetic.VIDEO_PATH
        )
    }
