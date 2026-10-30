"""REGISTER_SCENES against real PostgreSQL and real MinIO (ADR-015 §6.4,
MS-7): canonical manifests published to S3 at unit-scoped,
checksum-qualified keys; the durability and integrity gate checked against
the objects in MinIO, not only against ArtifactRecords; units and
revisions registered additively in one transaction; concurrent
registrations decided by the unique keys; no DatasetVersion read or
written.

Requires SCENEOPS_DATABASE_URL (a database migrated to head) and a
reachable MinIO; skips otherwise. Runs in the disposable database and
bucket of `make test-integration`; every test uses its own ids.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, update

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.datasets.schemas import DatasetRecord
from sceneops_core.jobs.schemas import ComposeDatasetVersionJobParams
from sceneops_core.provenance import RevisionDecision, manifest_artifact_id_for
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import (
    build_scene_manifest,
    payload_bytes,
    recording_source,
)
from sceneops_db.models.artifacts import ArtifactModel
from sceneops_db.models.datasets import DatasetVersionMemberModel, DatasetVersionModel
from sceneops_db.models.scenes import SceneModel, SceneRevisionModel
from sceneops_db.session import get_async_sessionmaker
from sceneops_storage import PayloadUriResolver
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    SceneRegistrationConflictError,
    register_scenes,
)

RECORDING_CHECKSUM = "sha256:" + "1" * 64


def _payload_id(ref) -> str:
    """The payload artifact id a recording payload's Logical URI names."""
    return ref.logical_uri().key.rpartition("/")[2]


class _Env:
    def __init__(self, settings, prefix: str) -> None:
        self.settings = settings
        self.prefix = prefix
        # The RobotRun every default manifest of this environment is built from.
        self.run_id = f"run-{prefix}"

    def source(self, unit_key: str = "whole_recording-x/recording", run_id=None):
        return recording_source(
            robot_run_id=run_id or self.run_id,
            unit_key=unit_key,
            recording_checksum=RECORDING_CHECKSUM,
        )

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    def manifest(self, **kwargs):
        kwargs.setdefault("payload_namespace", f"art-payload-{self.prefix}")
        kwargs.setdefault("source", self.source())
        return build_scene_manifest(**kwargs)

    def payload_uri(self, ref) -> str:
        """Where the recording builders store the payload ``ref`` names."""
        return PayloadUriResolver.from_settings(self.settings.artifact).resolve(ref.uri)

    async def register_payloads(self, ctx, manifest) -> None:
        """Producer side: payload bytes to MinIO plus their
        OBSERVATION_PAYLOAD ArtifactRecords (idempotent per artifact id)."""
        for observation in manifest.observations:
            ref = observation.payload
            if await ctx.artifact_record_store.get(_payload_id(ref)) is not None:
                continue
            uri = self.payload_uri(ref)
            await ctx.artifact_store.write_bytes(
                uri, payload_bytes(observation.observation_id)
            )
            await ctx.artifact_record_store.create(
                artifact_id=_payload_id(ref),
                ref=ArtifactRef(
                    kind=ArtifactKind.OBSERVATION_PAYLOAD,
                    uri=uri,
                    media_type=ref.media_type,
                    size_bytes=ref.size_bytes,
                    checksum=ref.checksum,
                ),
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=manifest.lineage.source.robot_run_id,
            )

    async def publish(self, manifest, *, with_payloads: bool = True) -> str:
        """Producer side: payload artifacts, then write-once manifest bytes
        to MinIO at the unit-scoped key plus the SCENE_MANIFEST
        ArtifactRecord under the revision's id, committed."""
        async with get_async_sessionmaker()() as session:
            ctx = self.context(session)
            if with_payloads:
                await self.register_payloads(ctx, manifest)
            scene_id = scene_id_for(manifest.lineage.source)
            published = await ctx.scene_artifact_store.publish_canonical_manifest(
                scene_id=scene_id, manifest=manifest
            )
            artifact_id = manifest_artifact_id_for(
                unit_id=scene_id, manifest_checksum=published.checksum
            )
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
                    scene_id=scene_id,
                )
            await session.commit()
            return artifact_id

    async def seed_robot_run(self, run_id: str) -> None:
        robot_id = f"robot-{run_id}"
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

    async def register(self, artifact_ids):
        async with get_async_sessionmaker()() as session:
            return await register_scenes(
                context=self.context(session), manifest_artifact_ids=artifact_ids
            )

    async def revisions(self, run_id=None) -> dict:
        """{(scene_id, manifest_checksum): revision} committed for a run."""
        async with get_async_sessionmaker()() as session:
            store = self.context(session).scene_store
            return {
                (r.scene_id, r.manifest_checksum): r
                for unit in await store.list(robot_run_id=run_id or self.run_id)
                for r in await store.list_revisions(unit.scene_id)
            }

    async def artifact(self, artifact_id):
        async with get_async_sessionmaker()() as session:
            return await self.context(session).artifact_record_store.get(artifact_id)


@pytest.fixture()
async def env(_fresh_database_connection, _minio_reachable, worker_settings, unique_id):
    from sceneops_worker.core import dependencies as dependencies_module

    dependencies_module._artifact_store = None
    environment = _Env(worker_settings, unique_id("scenes"))
    await environment.seed_robot_run(environment.run_id)
    yield environment
    dependencies_module._artifact_store = None


# --- revisions -----------------------------------------------------------------------


async def test_registration_lifecycle_on_postgres(env):
    a = await env.publish(env.manifest(source=env.source("a")))
    b = await env.publish(
        env.manifest(source=env.source("b"), keyframe_timestamps_ns=(1_000,))
    )

    created = await env.register([b, a])
    committed = await env.revisions()
    assert len(committed) == 2
    assert [p.unit_id for p in created.pins] == sorted(s for s, _ in committed)
    for pin in created.pins:
        assert committed[(pin.unit_id, pin.manifest_checksum)].manifest_artifact_id == (
            pin.manifest_artifact_id
        )
    assert {r.decision for r in created.revisions} == {RevisionDecision.NEW_UNIT}

    # Identical retry converges on the committed rows.
    retry = await env.register([a, b])
    assert retry.pins == created.pins
    assert {r.decision for r in retry.revisions} == {RevisionDecision.EXISTING_REVISION}
    assert await env.revisions() == committed

    # Another build configuration is a new revision of the same unit; the
    # first revision stays.
    rebuilt = await env.publish(
        env.manifest(source=env.source("a"), build_config={"channels": []})
    )
    (added,) = (await env.register([rebuilt])).revisions
    assert added.decision == RevisionDecision.NEW_REVISION
    after = await env.revisions()
    assert len(after) == 3
    assert {r.manifest_artifact_id for r in after.values()} == {a, b, rebuilt}


async def test_a_drifting_producer_is_refused_and_writes_nothing(env):
    first = await env.publish(env.manifest(source=env.source("a")))
    drifted = await env.publish(
        env.manifest(source=env.source("a"), keyframe_timestamps_ns=(1_000,))
    )
    await env.register([first])
    before = await env.revisions()

    with pytest.raises(SceneRegistrationConflictError, match="not deterministic"):
        await env.register([drifted])
    assert await env.revisions() == before


async def test_registration_is_atomic_on_postgres(env):
    await env.register([await env.publish(env.manifest(source=env.source("a")))])
    before = await env.revisions()

    # "c" is new and valid; "a" drifted. Neither is committed.
    batch = [
        await env.publish(
            env.manifest(source=env.source(key), keyframe_timestamps_ns=(7,))
        )
        for key in ("a", "c")
    ]
    with pytest.raises(SceneRegistrationConflictError):
        await env.register(batch)
    assert await env.revisions() == before


async def test_registration_never_touches_a_dataset_version(env, unique_id):
    """A DatasetVersion pinning a revision keeps exactly its rows when the
    unit gains another revision; registration writes no member, summary or
    version row."""
    dataset_id = unique_id("ds")
    first = await env.publish(env.manifest(source=env.source("a")))
    (pin,) = (await env.register([first])).pins
    async with get_async_sessionmaker()() as session:
        ctx = env.context(session)
        await ctx.dataset_store.create_dataset(DatasetRecord(dataset_id=dataset_id))
        await session.commit()
        await compose_dataset_version(
            context=ctx,
            params=ComposeDatasetVersionJobParams(
                dataset_id=dataset_id, dataset_version="v1", pins=[pin]
            ),
        )

    async def snapshot():
        async with get_async_sessionmaker()() as session:
            members = (
                await session.execute(select(DatasetVersionMemberModel))
            ).scalars()
            versions = (await session.execute(select(DatasetVersionModel))).scalars()
            return (
                sorted(
                    (m.dataset_version_id, m.unit_id, m.manifest_checksum)
                    for m in members
                ),
                sorted(
                    (v.id, v.composition_checksum, v.scene_count, v.episode_count)
                    for v in versions
                ),
            )

    before = await snapshot()
    rebuilt = await env.publish(
        env.manifest(source=env.source("a"), build_config={"channels": []})
    )
    await env.register([rebuilt])
    assert await snapshot() == before


async def test_registration_writes_no_object(env):
    manifest = env.manifest(source=env.source("a"))
    artifact_id = await env.publish(manifest)
    async with get_async_sessionmaker()() as session:
        store = env.context(session).artifact_store
        before = await store.list_objects(env.settings.artifact_root_uri)
        await env.register([artifact_id])
        await env.register([artifact_id])
        assert await store.list_objects(env.settings.artifact_root_uri) == before


# --- concurrency ---------------------------------------------------------------------


async def test_concurrent_identical_registrations_converge(env):
    artifacts = [
        await env.publish(env.manifest(source=env.source(key))) for key in ("a", "b")
    ]

    results = await asyncio.gather(env.register(artifacts), env.register(artifacts))

    assert results[0].pins == results[1].pins
    decisions = sorted(
        sorted(r.decision for r in result.revisions) for result in results
    )
    assert decisions == [
        [RevisionDecision.EXISTING_REVISION] * 2,
        [RevisionDecision.NEW_UNIT] * 2,
    ]
    assert len(await env.revisions()) == 2


async def test_overlapping_registrations_in_opposite_order_do_not_deadlock(env):
    """Inserts go in unit id order, so two registrations of the same units
    given in opposite orders wait for each other instead of deadlocking."""
    artifacts = [
        await env.publish(env.manifest(source=env.source(key)))
        for key in ("a", "b", "c", "d")
    ]

    for _ in range(3):
        results = await asyncio.gather(
            env.register(artifacts), env.register(list(reversed(artifacts)))
        )
        assert results[0].pins == results[1].pins
    assert len(await env.revisions()) == 4


async def test_concurrent_conflicting_revisions_have_one_winner(env):
    a1 = await env.publish(env.manifest(source=env.source("a")))
    a2 = await env.publish(
        env.manifest(source=env.source("a"), keyframe_timestamps_ns=(1_000,))
    )

    results = await asyncio.gather(
        env.register([a1]), env.register([a2]), return_exceptions=True
    )

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1
    assert isinstance(errors[0], SceneRegistrationConflictError)
    winner = next(r for r in results if not isinstance(r, Exception))
    (revision,) = (await env.revisions()).values()
    assert revision.manifest_artifact_id == winner.pins[0].manifest_artifact_id


# --- durability and integrity gate ---------------------------------------------------


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
    assert await env.revisions() == {}


async def test_a_manifest_object_missing_from_minio_is_rejected(env):
    artifact_id = await env.publish(env.manifest())
    async with get_async_sessionmaker()() as session:
        ctx = env.context(session)
        await ctx.artifact_store.delete_prefix((await env.artifact(artifact_id)).uri)

    with pytest.raises(SceneManifestRejectedError, match="not found"):
        await env.register([artifact_id])
    assert await env.revisions() == {}


async def test_a_payload_object_missing_from_minio_is_rejected(env):
    """The record exists with the referenced integrity metadata, but the
    object is gone: the gate, not the record, decides."""
    manifest = env.manifest()
    artifact_id = await env.publish(manifest)
    payload = manifest.observations[-1].payload
    payload_id = _payload_id(payload)
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_store.delete_prefix(
            env.payload_uri(payload)
        )
    assert await env.artifact(payload_id) is not None

    with pytest.raises(SceneManifestRejectedError, match="has no object"):
        await env.register([artifact_id])
    assert await env.revisions() == {}


async def test_a_payload_object_of_another_size_is_rejected(env):
    manifest = env.manifest()
    artifact_id = await env.publish(manifest)
    payload = manifest.observations[0].payload
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_store.write_bytes(
            env.payload_uri(payload), b"truncated"
        )

    with pytest.raises(SceneManifestRejectedError, match="has 9 bytes; the reference"):
        await env.register([artifact_id])
    assert await env.revisions() == {}


async def test_payload_refs_are_checked_against_artifact_records_on_postgres(env):
    unregistered = await env.publish(
        env.manifest(source=env.source("a")), with_payloads=False
    )
    with pytest.raises(SceneManifestRejectedError, match="not registered"):
        await env.register([unregistered])

    # A payload ArtifactRecord whose integrity metadata differs from the
    # reference (here: same id, different bytes) is not the referenced payload.
    manifest = env.manifest(source=env.source("b"))
    payload = manifest.observations[0].payload
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_record_store.create(
            artifact_id=_payload_id(payload),
            ref=ArtifactRef(
                kind=ArtifactKind.OBSERVATION_PAYLOAD,
                uri=env.payload_uri(payload),
                media_type=payload.media_type,
                size_bytes=payload.size_bytes,
                checksum="sha256:" + "0" * 64,
            ),
        )
        await session.commit()
    mismatched = await env.publish(manifest)
    with pytest.raises(SceneManifestRejectedError, match="checksum"):
        await env.register([mismatched])
    assert await env.revisions() == {}


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
    payload_id = _payload_id(manifest.observations[0].payload)
    async with get_async_sessionmaker()() as session:
        await session.execute(
            update(ArtifactModel)
            .where(ArtifactModel.artifact_id == payload_id)
            .values({column: value})
        )
        await session.commit()

    with pytest.raises(SceneManifestRejectedError, match=message):
        await env.register([artifact_id])
    assert await env.revisions() == {}


async def test_a_rejected_registration_leaves_no_unit_row(env):
    """A gate failure happens before the transaction: no unit row exists for
    a unit whose bytes were never verified."""
    manifest = env.manifest(source=env.source("z"))
    artifact_id = await env.publish(manifest)
    async with get_async_sessionmaker()() as session:
        await env.context(session).artifact_store.delete_prefix(
            env.payload_uri(manifest.observations[0].payload)
        )
    with pytest.raises(SceneManifestRejectedError):
        await env.register([artifact_id])

    scene_id = scene_id_for(manifest.lineage.source)
    async with get_async_sessionmaker()() as session:
        units = await session.scalar(
            select(func.count()).where(SceneModel.scene_id == scene_id)
        )
        revisions = await session.scalar(
            select(func.count()).where(SceneRevisionModel.scene_id == scene_id)
        )
    assert (units, revisions) == (0, 0)


# --- manifests ------------------------------------------------------------------------


async def test_one_build_is_one_revision_at_a_unit_scoped_key(env):
    """Unit identity does not depend on any DatasetVersion: a second build of
    the same bytes reuses the manifest object, the ArtifactRecord and the
    revision."""
    manifest = env.manifest(source=env.source("a"))
    first = await env.publish(manifest)
    uri = (await env.artifact(first)).uri
    assert f"/canonical/scenes/{scene_id_for(manifest.lineage.source)}/" in uri
    again = await env.publish(manifest)
    assert again == first

    one = await env.register([first])
    two = await env.register([again])

    assert one.pins == two.pins
    assert len(await env.revisions()) == 1
    artifact = await env.artifact(first)
    assert (artifact.dataset_id, artifact.dataset_version) == (None, None)
