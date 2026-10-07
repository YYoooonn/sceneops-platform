"""EXPORT_ANALYTICS_SNAPSHOT over registered Scenes: observation-centric
tables built from each Scene's verified current revision."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import polars as pl
import pytest

from sceneops_analytics import AnalyticsTableWriter
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
    scene_world.add_dataset_version()
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
    await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                dataset_id="ds",
                dataset_version="v1",
                manifest_artifact_ids=[a.artifact_id for a in artifacts],
            ),
            context=world.context,
        )
    )


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
    assert set(observations["scene_id"].to_list()) == set(world.scenes.committed)
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

    (scene_id,) = world.scenes.working
    record = world.scenes.working[scene_id]
    world.scenes.working[scene_id] = record.model_copy(
        update={"observation_count": record.observation_count + 1}
    )
    second = await _export(world, tables=["scenes"])

    assert second.table_uris["scenes"] != old_uri
    # The earlier revision is untouched and still what its record pins.
    assert await world.artifact_store.read_bytes(old_uri) == old_bytes
    records = {r.uri: r for r in _table_records(world)}
    assert records[old_uri].checksum == sha256_checksum(old_bytes)
    assert len(records) == 2
