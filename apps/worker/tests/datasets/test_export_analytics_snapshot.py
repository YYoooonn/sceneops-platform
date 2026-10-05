"""EXPORT_ANALYTICS_SNAPSHOT over registered Scenes: observation-centric
tables built from each Scene's verified current revision."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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
    tables = {}

    async def write_table(name, df, *, dataset_id, dataset_version):
        tables[name] = df
        return f"{scene_world.root}/analytics/{name}.parquet"

    scene_world.context.analytics_writer.write_table = AsyncMock(
        side_effect=write_table
    )
    scene_world.tables = tables
    return scene_world


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
    observations = world.tables["observations"]
    assert set(observations["scene_id"].to_list()) == set(world.scenes.committed)
    assert observations["timestamp_ns"].dtype.is_integer()


async def test_respects_requested_table_subset(world):
    await _register(world, "a")
    result = await _export(world, tables=["scenes"])
    assert set(result.table_uris) == {"scenes"}


async def test_raises_if_no_registered_scenes(world):
    with pytest.raises(ValueError, match="no registered scenes"):
        await _export(world)
