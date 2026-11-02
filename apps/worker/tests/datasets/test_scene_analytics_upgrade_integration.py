"""An upgrade of the Scene analytics schema on real PostgreSQL and real MinIO. One
DatasetVersion pins Scenes of a RobotRun and of a preserved nuScenes content:

* every row states the clock instance its own canonical manifest declares, and the
  scenes table its own source;
* a v2 snapshot lands beside the v1 objects an earlier deployment left, which stay
  exactly as they were and are refused by today's reader;
* a Job that succeeded before the schema entered its key is not the answer to a
  request now; a repeated current request converges on one Job, and a forced one on
  the same objects and records;
* an export that wrote its tables and failed before registering them converges when
  run again, and concurrent exports converge on one object and one record.

The v1 objects are the exact bytes the old writer produced (see
``sceneops_analytics.testing.scene_analytics_migration``). Handlers run in process:
this proves storage, database and reuse semantics, not a deployed worker. The
RobotRun's revision is seeded through the repositories, with its manifest really on
MinIO; the content goes through the production path.

Requires the disposable database and bucket of `make test-integration`; skips (and
so fails there) otherwise.
"""

from __future__ import annotations

import asyncio
import io

import polars as pl
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from sceneops_analytics import (
    AnalyticsTableWriter,
    SceneRevisionContent,
    UnsupportedSceneAnalyticsSchemaError,
    read_scene_table_bytes,
)
from sceneops_analytics.testing.scene_analytics_migration import write_v1_scene_tables
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetRecord
from sceneops_core.executions import compute_execution_key
from sceneops_core.jobs.schemas import (
    ComposeDatasetVersionJobParams,
    CreateJobRequest,
    ExportAnalyticsSnapshotJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_core.scenes.schemas import (
    load_canonical_scene_manifest,
    project_scene_revision,
    scene_id_for,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.temporal import (
    IncomparableClocksError,
    require_comparable,
    restore_clock_instance,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.datasets import PostgresDatasetRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_execution.jobs.service import JobService
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset import export_analytics_snapshot as export_module
from sceneops_worker.jobs.dataset.export_analytics_snapshot import (
    ExportAnalyticsSnapshotJobHandler,
)
from tests.datasets.test_dataset_composition_integration import (
    RECORDING_CHECKSUM,
    _seed_run,
)
from tests.datasets.test_mixed_source_composition_integration import (
    SCENE_CONFIG,
    _Mixed,
)

VERSION = "analytics-upgrade"
CLOCK_COLUMNS = {
    "scenes": ("window_clock", "window_clock_origin", "window_clock_scope"),
    "observations": ("source_clock", "clock_origin", "clock_scope"),
    "keyframes": ("source_clock", "clock_origin", "clock_scope"),
}


def _service(session) -> JobService:
    return JobService(
        repository=PostgresJobRepository(session),
        event_repository=PostgresJobEventRepository(session),
        artifact_repository=PostgresArtifactRefRepository(session),
        episode_repository=PostgresEpisodeRepository(session),
    )


class _Env(_Mixed):
    """A RobotRun with one Scene and a nuScenes content with two, all registered
    and composed into one DatasetVersion."""

    def __init__(self, settings, run_id: str, tmp_path) -> None:
        super().__init__(None, settings, run_id, tmp_path)
        self.dataset_id = f"ds-{run_id}"
        self.robot_run_id = f"run-{run_id}"

    @property
    def writer(self) -> AnalyticsTableWriter:
        return AnalyticsTableWriter(
            artifact_store=self.store, root_uri=self.settings.analytics_root_uri
        )

    async def _seed_recording_scene(self):
        source = recording_source(
            robot_run_id=self.robot_run_id,
            unit_key="segment-a",
            recording_checksum=RECORDING_CHECKSUM,
        )
        manifest = build_scene_manifest(
            source=source, source_clock="sensor.lidar", camera_clock="sensor.camera"
        )
        scene_id = scene_id_for(source)
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            await _seed_run(session, self.robot_run_id)
            published = await context.scene_artifact_store.publish_canonical_manifest(
                scene_id=scene_id, manifest=manifest
            )
            artifact_id = manifest_artifact_id_for(
                unit_id=scene_id, manifest_checksum=published.checksum
            )
            await PostgresArtifactRefRepository(session).create(
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
            revision = project_scene_revision(
                manifest=manifest,
                manifest_artifact_id=artifact_id,
                manifest_checksum=published.checksum,
            )
            await PostgresSceneRepository(session).register_revision(revision)
            await session.commit()
        return revision.pin()

    async def compose(self) -> None:
        pins = [await self._seed_recording_scene()]
        self.snapshot["nuscenes"], self.content["nuscenes"] = await self.publish(
            "nuscenes"
        )
        built = await self.build("nuscenes", self.snapshot["nuscenes"], SCENE_CONFIG)
        registration = await self.register("nuscenes", built.manifest_artifact_ids)
        pins += [r.pin for r in registration.revisions]
        async with get_async_sessionmaker()() as session:
            await PostgresDatasetRepository(session).create(
                DatasetRecord(dataset_id=self.dataset_id)
            )
            await session.commit()
        async with get_async_sessionmaker()() as session:
            await compose_dataset_version(
                context=self.context(session),
                params=ComposeDatasetVersionJobParams.model_validate(
                    {
                        "dataset_id": self.dataset_id,
                        "dataset_version": VERSION,
                        "pins": [p.model_dump(mode="json") for p in pins],
                    }
                ),
            )

    def request(self, **overrides) -> CreateJobRequest:
        return CreateJobRequest(
            type=JobType.EXPORT_ANALYTICS_SNAPSHOT,
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            params={},
            **overrides,
        )

    async def create(self, request: CreateJobRequest) -> JobManifest:
        async with get_async_sessionmaker()() as session:
            job = await _service(session).create_job(request)
            await session.commit()
            return job

    async def run_export(self, job: JobManifest | None = None):
        self.jobs += 1
        params = ExportAnalyticsSnapshotJobParams(
            dataset_id=self.dataset_id, dataset_version=VERSION
        )
        job = job or JobManifest(
            job_id=f"job-{self.run_id}-{self.jobs}",
            type=JobType.EXPORT_ANALYTICS_SNAPSHOT,
            status=JobStatus.RUNNING,
            params=params.model_dump(mode="json"),
        )
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            result = await ExportAnalyticsSnapshotJobHandler().run(
                JobHandlerRequest(job=job, params=params, context=context)
            )
            await context.commit()
        return result

    async def member_scenes(self) -> list:
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            return await context.scene_store.list_member_revisions(
                dataset_id=self.dataset_id, dataset_version=VERSION, limit=100
            )

    async def manifests(self) -> dict:
        by_scene = {}
        for record in await self.member_scenes():
            async with get_async_sessionmaker()() as session:
                artifact = await self.context(session).artifact_record_store.get(
                    record.manifest_artifact_id
                )
            by_scene[record.scene_id] = load_canonical_scene_manifest(
                await self.store.read_bytes(artifact.uri)
            )
        return by_scene

    async def objects(self) -> dict[str, bytes]:
        root = self.settings.analytics_root_uri
        return {
            item.uri: await self.store.read_bytes(item.uri)
            for item in await self.store.list_objects(root)
        }

    async def table_records(self) -> dict[str, dict]:
        async with get_async_sessionmaker()() as session:
            rows = await session.execute(
                text(
                    "SELECT artifact_id, uri, checksum, metadata FROM artifacts "
                    "WHERE dataset_id = :id AND kind = :kind"
                ),
                {"id": self.dataset_id, "kind": ArtifactKind.ANALYTICS_TABLE.value},
            )
            return {row.uri: dict(row._mapping) for row in rows}


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    environment = _Env(worker_settings, unique_id("analytics"), tmp_path)
    await environment.compose()
    yield environment
    dependencies_module._artifact_store = None


async def test_every_row_states_what_its_own_manifest_declares(env):
    result = await env.run_export()
    manifests = await env.manifests()
    assert result.scene_count == 3 and len(manifests) == 3

    tables = {name: await env.writer.read_table(name, uri) for name, uri in result.table_uris.items()}  # fmt: skip
    assert {name: t.height for name, t in tables.items()} == result.row_counts
    for table_name, table in tables.items():
        clock_column, origin_column, scope_column = CLOCK_COLUMNS[table_name]
        for row in table.to_dicts():
            manifest = manifests[row["scene_id"]]
            assert restore_clock_instance(
                row[clock_column], row[origin_column], row[scope_column]
            ) == manifest.clock_instance(row[clock_column])

    records = {r.scene_id: r for r in await env.member_scenes()}
    rows = {r["scene_id"]: r for r in tables["scenes"].to_dicts()}
    assert {r["source_kind"] for r in rows.values()} == {"recording", "content"}
    for scene_id, row in rows.items():
        record = records[scene_id]
        assert (row["source_kind"], row["robot_run_id"], row["content_id"]) == (
            record.source_kind,
            record.robot_run_id,
            record.content_id,
        )
        assert (row["manifest_checksum"], row["manifest_artifact_id"]) == (
            record.manifest_checksum,
            record.manifest_artifact_id,
        )
    recording = next(r for r in rows.values() if r["robot_run_id"] == env.robot_run_id)
    assert recording["content_id"] is None and recording["source_kind"] == "recording"
    native = [r for r in rows.values() if r["content_id"] == env.content["nuscenes"]]
    assert len(native) == 2 and {r["robot_run_id"] for r in native} == {None}

    # The Recording Scene's window clock is one no observation of it uses, and its
    # observations are on two others: each row carries its own.
    observations = tables["observations"].filter(
        tables["observations"]["scene_id"] == recording["scene_id"]
    )
    assert recording["window_clock"] == "mcap_log_time"
    assert set(observations["source_clock"]) == {"sensor.camera", "sensor.lidar"}
    assert {tuple(s) for s in observations["clock_scope"].to_list()} == {
        ("recording", env.robot_run_id)
    }

    records = await env.table_records()
    for table_name, uri in result.table_uris.items():
        data = await env.store.read_bytes(uri)
        assert pq.read_metadata(io.BytesIO(data)).metadata[
            b"sceneops.table_schema"
        ] == (f"scene_analytics/v2/{table_name}".encode())
        assert records[uri]["metadata"] == {
            "table_name": table_name,
            "table_schema": f"scene_analytics/v2/{table_name}",
        }


async def test_two_native_scenes_compare_only_as_their_manifests_declare(env):
    """Whatever origin the builder declared, the persisted answer is the manifest's."""
    result = await env.run_export()
    manifests = await env.manifests()
    rows = (
        await env.writer.read_table("scenes", result.table_uris["scenes"])
    ).to_dicts()
    native = [r for r in rows if r["content_id"] == env.content["nuscenes"]]
    a, b = [
        restore_clock_instance(
            r["window_clock"], r["window_clock_origin"], r["window_clock_scope"]
        )
        for r in native
    ]
    declared_a = manifests[native[0]["scene_id"]].clock_instance(a.clock)
    declared_b = manifests[native[1]["scene_id"]].clock_instance(b.clock)
    assert (a, b) == (declared_a, declared_b)
    recording = next(r for r in rows if r["robot_run_id"] == env.robot_run_id)
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            a,
            restore_clock_instance(
                recording["window_clock"],
                recording["window_clock_origin"],
                recording["window_clock_scope"],
            ),
        )


async def test_v2_lands_beside_a_real_v1_snapshot_which_stays_as_it_was(env):
    manifests = await env.manifests()
    contents = [
        SceneRevisionContent(record=record, manifest=manifests[record.scene_id])
        for record in await env.member_scenes()
    ]
    old = await write_v1_scene_tables(
        env.writer, dataset_id=env.dataset_id, dataset_version=VERSION, scenes=contents
    )
    v1_objects = await env.objects()
    assert set(v1_objects) == {t.uri for t in old.values()}

    result = await env.run_export()

    objects = await env.objects()
    for uri, data in v1_objects.items():
        assert objects[uri] == data
    assert set(objects) == set(v1_objects) | set(result.table_uris.values())
    for table_name, table in old.items():
        assert result.table_uris[table_name] != table.uri
        with pytest.raises(UnsupportedSceneAnalyticsSchemaError):
            await env.writer.read_table(table_name, table.uri)
        current = read_scene_table_bytes(
            objects[result.table_uris[table_name]],
            table_name,
            uri=result.table_uris[table_name],
        )
        v1_rows = pl.read_parquet(io.BytesIO(v1_objects[table.uri]))
        assert current.select(v1_rows.columns).equals(v1_rows)

    # Repeating converges: no object and no record is added.
    records = await env.table_records()
    repeat = await env.run_export()
    assert repeat.table_uris == result.table_uris
    assert await env.objects() == objects
    assert await env.table_records() == records


async def test_a_succeeded_old_schema_job_is_not_reused_on_real_postgres(env):
    async with get_async_sessionmaker()() as session:
        probe = await _service(session).create_job(env.request())
        await session.rollback()

    # What an earlier deployment's Job has in its execution_key column: the same
    # request hashed without the schema.
    legacy_key = compute_execution_key(
        kind="job",
        type=JobType.EXPORT_ANALYTICS_SNAPSHOT.value,
        dataset_id=env.dataset_id,
        dataset_version=VERSION,
        params=probe.params,
    )
    assert legacy_key != probe.execution_key
    now = utc_now()
    async with get_async_sessionmaker()() as session:
        old = await PostgresJobRepository(session).create(
            probe.model_copy(
                update={
                    "job_id": f"job-{env.run_id}-old-schema",
                    "status": JobStatus.SUCCEEDED,
                    "execution_key": legacy_key,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        )
        await session.commit()

    current = await env.create(env.request())
    assert current.job_id != old.job_id
    assert current.execution_key == probe.execution_key
    assert current.status == JobStatus.PENDING

    result = await env.run_export(current)
    assert set(result.table_uris) == {"scenes", "observations", "keyframes"}
    objects = await env.objects()
    records = await env.table_records()

    # In flight and once it has succeeded, the request stays idempotent.
    assert (await env.create(env.request())).job_id == current.job_id
    async with get_async_sessionmaker()() as session:
        await session.execute(
            text("UPDATE jobs SET status = :status WHERE job_id = :job_id"),
            {"status": JobStatus.SUCCEEDED.value, "job_id": current.job_id},
        )
        await session.commit()
    assert (await env.create(env.request())).job_id == current.job_id

    # Forcing runs it again and converges on the same objects and records.
    forced = await env.create(env.request(force=True))
    assert forced.job_id not in {current.job_id, old.job_id}
    assert (await env.run_export(forced)).table_uris == result.table_uris
    assert await env.objects() == objects
    assert await env.table_records() == records

    # The canonical pins the Job read did not move.
    assert {r.manifest_checksum for r in await env.member_scenes()} == {
        m.checksum() for m in (await env.manifests()).values()
    }


async def test_a_failure_after_the_tables_were_written_converges_when_run_again(
    env, monkeypatch
):
    class CommitLost(RuntimeError):
        pass

    async def lost(*_, **__):
        raise CommitLost

    original = export_module.register_published
    monkeypatch.setattr(export_module, "register_published", lost)
    with pytest.raises(CommitLost):
        await env.run_export()
    written = await env.objects()
    assert len(written) == 3
    assert await env.table_records() == {}

    monkeypatch.setattr(export_module, "register_published", original)
    result = await env.run_export()
    assert set(result.table_uris.values()) == set(written)
    assert await env.objects() == written
    assert set(await env.table_records()) == set(written)


async def test_concurrent_exports_converge_on_one_object_and_one_record(env):
    results = await asyncio.gather(*(env.run_export() for _ in range(3)))
    assert len({tuple(sorted(r.table_uris.items())) for r in results}) == 1
    assert len(await env.objects()) == 3
    assert len(await env.table_records()) == 3
