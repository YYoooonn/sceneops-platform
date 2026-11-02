"""EXPORT_ANALYTICS_SNAPSHOT over registered Scenes: observation-centric
tables built from each Scene's verified current revision."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import polars as pl
import pyarrow.parquet as pq
import pytest

from sceneops_analytics import (
    AnalyticsTableWriter,
    SceneRevisionPairingError,
    read_scene_table_bytes,
)
from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.common.checksums import sha256_checksum

from sceneops_core.jobs.schemas import (
    ExportAnalyticsSnapshotJobParams,
    RegisterScenesJobParams,
)
from sceneops_core.scenes.testing import recording_source
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.export_analytics_snapshot import (
    ExportAnalyticsSnapshotJobHandler,
)
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler


def _job() -> MagicMock:
    job = MagicMock()
    job.job_id = "job-1"
    job.pipeline_run_id = "pipe-1"
    return job


@pytest.fixture()
def world(scene_world):
    scene_world.context.analytics_writer = AnalyticsTableWriter(
        artifact_store=scene_world.artifact_store,
        root_uri=f"{scene_world.root}/analytics",
    )
    return scene_world


async def _table(world, uri: str) -> pl.DataFrame:
    return pl.read_parquet(io.BytesIO(await world.artifact_store.read_bytes(uri)))


def _table_records(world):
    return [
        r
        for r in world.artifacts.values()
        if r.kind == ArtifactKind.ANALYTICS_TABLE.value
    ]


async def _register(world, *keys):
    artifacts = [
        await world.publish(world.manifest(source=recording_source(unit_key=k)))
        for k in keys
    ]
    result = await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                manifest_artifact_ids=[a.artifact_id for a in artifacts],
            ),
            context=world.context,
        )
    )
    world.scenes.pin(result.revisions)


async def _export(world, tables=None):
    return await ExportAnalyticsSnapshotJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=ExportAnalyticsSnapshotJobParams(
                dataset_id="ds", dataset_version="v1", tables=tables
            ),
            context=world.context,
        )
    )


async def test_exports_all_three_tables_by_default(world):
    await _register(world, "a", "b")
    result = await _export(world)

    assert set(result.table_uris) == {
        "scenes",
        "observations",
        "keyframes",
    }
    assert result.row_counts == {
        "scenes": 2,
        "observations": 10,
        "keyframes": 4,
    }
    observations = await _table(world, result.table_uris["observations"])
    assert set(observations["scene_id"].to_list()) == set(world.scenes.members())
    assert observations["timestamp_ns"].dtype.is_integer()


async def test_respects_requested_table_subset(world):
    await _register(world, "a")
    result = await _export(world, tables=["scenes"])
    assert set(result.table_uris) == {"scenes"}


async def test_raises_if_no_registered_scenes(world):
    with pytest.raises(ValueError, match="no registered scenes"):
        await _export(world)


async def test_each_record_pins_exactly_the_bytes_at_its_uri(world):
    await _register(world, "a", "b")
    result = await _export(world)

    records = {r.uri: r for r in _table_records(world)}
    assert set(records) == set(result.table_uris.values())
    for uri, record in records.items():
        data = await world.artifact_store.read_bytes(uri)
        assert record.checksum == sha256_checksum(data)
        assert record.size_bytes == len(data)


async def test_re_exporting_unchanged_data_converges(world):
    await _register(world, "a", "b")
    first = await _export(world)
    ids = {r.artifact_id for r in _table_records(world)}

    again = await _export(world)

    assert again.table_uris == first.table_uris
    assert {r.artifact_id for r in _table_records(world)} == ids


async def test_exporting_changed_data_adds_a_revision_and_keeps_the_old_one(world):
    await _register(world, "a")
    first = await _export(world, tables=["scenes"])
    old_uri = first.table_uris["scenes"]
    old_bytes = await world.artifact_store.read_bytes(old_uri)

    # The same unit at another revision, under the same table's logical id. A real
    # version never changes its pins: this stands in for any change of the bytes.
    artifact = await world.publish(
        world.manifest(
            source=recording_source(unit_key="a"),
            semantics_version=2,
            keyframe_timestamps_ns=(1_000_000_000,),
        )
    )
    registered = await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                manifest_artifact_ids=[artifact.artifact_id]
            ),
            context=world.context,
        )
    )
    ((pin,),) = (registered.revisions,)
    world.scenes.working[1][("ds", "v1")][pin.unit_id] = pin.manifest_checksum
    second = await _export(world, tables=["scenes"])

    assert second.table_uris["scenes"] != old_uri
    # The earlier revision is untouched and still what its record pins.
    assert await world.artifact_store.read_bytes(old_uri) == old_bytes
    records = {r.uri: r for r in _table_records(world)}
    assert records[old_uri].checksum == sha256_checksum(old_bytes)
    assert len(records) == 2


async def test_a_failed_table_fails_the_export_before_any_table_is_written(
    world, monkeypatch
):
    """A table that cannot be built refuses the whole export; the scenes
    table, which could have been built, must not be written first."""
    from sceneops_worker.jobs.dataset import export_analytics_snapshot

    class TableBuildError(ValueError):
        pass

    def refuse(**_):
        raise TableBuildError("the observation table cannot be built")

    monkeypatch.setitem(
        export_analytics_snapshot._TABLE_BUILDERS, "observations", refuse
    )
    await _register(world, "a")
    before = await world.artifact_store.list_objects(f"{world.root}/analytics")
    with pytest.raises(TableBuildError):
        await _export(world)
    assert await world.artifact_store.list_objects(f"{world.root}/analytics") == before
    assert _table_records(world) == []


async def test_every_table_states_its_schema(world):
    await _register(world, "a")
    result = await _export(world)

    for table_name, uri in result.table_uris.items():
        data = await world.artifact_store.read_bytes(uri)
        footer = pq.read_metadata(io.BytesIO(data))
        assert footer.metadata[b"sceneops.table_schema"] == (
            f"scene_analytics/v2/{table_name}".encode()
        )
        assert read_scene_table_bytes(data, table_name, uri=uri).height > 0


async def test_a_record_the_manifest_does_not_project_refuses_the_export(world):
    """The pin is verified and the manifest read, but the record's projection of it
    is not what the manifest determines: no table is written from either."""
    await _register(world, "a")
    ((key, record),) = world.scenes.working[0].items()
    world.scenes.working[0][key] = record.model_copy(
        update={"observation_count": record.observation_count + 1}
    )
    with pytest.raises(SceneRevisionPairingError):
        await _export(world, tables=["scenes"])
    assert _table_records(world) == []


async def test_a_second_clock_exports_as_each_row_declares_it(world):
    manifest = world.manifest(
        source=recording_source(unit_key="a"),
        source_clock="sensor.lidar",
        camera_clock="sensor.camera",
    )
    artifact = await world.publish(manifest)
    registered = await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                manifest_artifact_ids=[artifact.artifact_id]
            ),
            context=world.context,
        )
    )
    world.scenes.pin(registered.revisions)
    result = await _export(world)

    scene = (await _table(world, result.table_uris["scenes"])).row(0, named=True)
    assert scene["window_clock"] == "mcap_log_time"
    observations = await _table(world, result.table_uris["observations"])
    keyframes = await _table(world, result.table_uris["keyframes"])
    assert set(observations["source_clock"]) == {"sensor.camera", "sensor.lidar"}
    assert set(keyframes["source_clock"]) == {"sensor.lidar"}
    for row in [*observations.to_dicts(), *keyframes.to_dicts()]:
        instance = manifest.clock_instance(row["source_clock"])
        assert (row["clock_origin"], tuple(row["clock_scope"])) == (
            instance.origin.value,
            instance.scope,
        )
