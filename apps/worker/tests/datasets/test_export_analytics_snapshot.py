"""Unit tests for ExportAnalyticsSnapshotJobHandler.

Follows the MagicMock WorkerContext convention used by
test_build_dataset_manifest.py, but returns real ``SceneManifest`` pydantic
objects from ``load_scene_manifest`` (rather than MagicMock) since the
handler flattens nested ``samples`` / ``sensor_frames`` / ``annotations``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.scenes.schemas import (
    SceneAnnotationManifest,
    SceneManifest,
    SceneRecord,
    SceneSampleManifest,
    SceneSensorFrameManifest,
    SceneStatus,
)
from sceneops_worker.jobs.dataset.export_analytics_snapshot import (
    ExportAnalyticsSnapshotJobHandler,
)

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


def _scene_record(scene_id: str) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        status=SceneStatus.PROFILED,
        sample_count=1,
        frame_count=2,
        annotation_count=1,
        scene_manifest_uri=f"file:///scenes/{scene_id}/manifest.json",
        channels=["CAM_FRONT", "LIDAR_TOP"],
    )


def _scene_manifest(scene_id: str) -> SceneManifest:
    frame = SceneSensorFrameManifest(
        frame_id=f"{scene_id}-frame-0",
        sample_id=f"{scene_id}-sample-0",
        timestamp_us=1000,
        channel="CAM_FRONT",
        uri=f"file:///{scene_id}/cam_front/0.jpg",
    )
    annotation = SceneAnnotationManifest(
        annotation_id=f"{scene_id}-ann-0",
        sample_id=f"{scene_id}-sample-0",
        category="vehicle.car",
    )
    sample = SceneSampleManifest(
        sample_id=f"{scene_id}-sample-0",
        scene_id=scene_id,
        timestamp_us=1000,
        sensor_frames=[frame],
        annotations=[annotation],
    )
    return SceneManifest(
        scene_id=scene_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        samples=[sample],
    )


def _job(job_id: str = "job-001") -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.pipeline_run_id = None
    return j


def _params(tables: list[str] | None = None) -> MagicMock:
    p = MagicMock()
    p.dataset_id = DATASET_ID
    p.dataset_version = DATASET_VERSION
    p.tables = tables
    return p


def _context(scene_records: list[SceneRecord]) -> MagicMock:
    ctx = MagicMock()

    ctx.scene_store = MagicMock()
    ctx.scene_store.list = AsyncMock(return_value=scene_records)

    manifest_map = {
        r.scene_manifest_uri: _scene_manifest(r.scene_id)
        for r in scene_records
        if r.scene_manifest_uri is not None
    }

    async def load_scene_manifest(uri: str):
        return manifest_map.get(uri)

    ctx.scene_artifact_store = MagicMock()
    ctx.scene_artifact_store.load_scene_manifest = load_scene_manifest

    written: dict[str, tuple] = {}

    async def write_table(table_name, df, *, dataset_id, dataset_version):
        uri = f"file:///analytical/{dataset_id}/{dataset_version}/{table_name}.parquet"
        written[table_name] = (df, uri)
        return uri

    ctx.analytics_writer = MagicMock()
    ctx.analytics_writer.write_table = AsyncMock(side_effect=write_table)
    ctx._written = written

    ctx.artifact_record_store = MagicMock()
    ctx.artifact_record_store.create = AsyncMock(return_value=MagicMock())

    return ctx


async def test_exports_all_four_tables_by_default():
    scenes = [_scene_record("scene-a"), _scene_record("scene-b")]
    ctx = _context(scenes)

    handler = ExportAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params()
    request.context = ctx

    result = await handler.run(request)

    assert set(result.table_uris) == {
        "scenes",
        "samples",
        "sensor_frames",
        "annotations",
    }
    assert result.row_counts["scenes"] == 2
    assert result.row_counts["samples"] == 2  # one sample per scene
    assert result.row_counts["sensor_frames"] == 2  # one frame per sample
    assert result.row_counts["annotations"] == 2  # one annotation per sample
    assert result.scene_count == 2
    assert ctx.artifact_record_store.create.call_count == 4


async def test_respects_requested_table_subset():
    scenes = [_scene_record("scene-a")]
    ctx = _context(scenes)

    handler = ExportAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params(tables=["scenes"])
    request.context = ctx

    result = await handler.run(request)

    assert set(result.table_uris) == {"scenes"}
    # scene manifests should not even be loaded for a scenes-only export
    assert ctx.artifact_record_store.create.call_count == 1


async def test_raises_if_no_registered_scenes():
    ctx = _context([])

    handler = ExportAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params()
    request.context = ctx

    with pytest.raises(ValueError, match="no registered scenes found"):
        await handler.run(request)
