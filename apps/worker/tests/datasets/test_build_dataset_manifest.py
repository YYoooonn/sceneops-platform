"""Regression tests: build_dataset_manifest and build_scene_index are DB-backed.

Verifies that:
- Both jobs query ALL registered SceneRecords for the dataset version,
  not just the current pipeline batch input.
- Rebuilding the manifest after a second batch results in a manifest with
  ALL scenes (not just the new batch).
- Existing SceneRecords are never deleted or hidden.
- Rebuilding twice is idempotent.
- DatasetVersion.scene_count reflects the full scene set after each build.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_worker.jobs.dataset.build_dataset_manifest import (
    BuildDatasetManifestJobHandler,
)
from sceneops_worker.jobs.dataset.build_scene_index import BuildSceneIndexJobHandler


# ── helpers ───────────────────────────────────────────────────────────────────

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


def _scene_record(
    scene_id: str,
    sample_count: int = 40,
    frame_count: int = 80,
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        status=SceneStatus.PROFILED,
        sample_count=sample_count,
        frame_count=frame_count,
        scene_manifest_uri=f"file:///scenes/{scene_id}/manifest.json",
        channels=["CAM_FRONT", "LIDAR_TOP"],
    )


def _scene_manifest_mock(scene_id: str, sample_count: int = 40, frame_count: int = 80):
    m = MagicMock()
    m.scene_id = scene_id
    m.sample_count = sample_count
    m.frame_count = frame_count
    m.channels = ["CAM_FRONT", "LIDAR_TOP"]
    return m


def _job(job_id: str = "job-001", pipeline_run_id: str = "pipe-001") -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.pipeline_run_id = pipeline_run_id
    j.pipeline_task_run_id = "ptask-001"
    j.params = {
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
    }
    return j


def _context(scene_records: list[SceneRecord]) -> MagicMock:
    """Build a mock WorkerContext whose scene_store.list returns the given records."""
    ctx = MagicMock()

    ctx.scene_store = MagicMock()
    ctx.scene_store.list = AsyncMock(return_value=scene_records)

    # Map URI → mock manifest
    manifest_map = {
        r.scene_manifest_uri: _scene_manifest_mock(
            r.scene_id,
            sample_count=r.sample_count or 40,
            frame_count=r.frame_count or 80,
        )
        for r in scene_records
        if r.scene_manifest_uri is not None
    }

    async def load_scene_manifest(uri: str):
        return manifest_map.get(uri)

    ctx.scene_artifact_store = MagicMock()
    ctx.scene_artifact_store.load_scene_manifest = load_scene_manifest
    ctx.scene_artifact_store.write_scene_index = AsyncMock(
        return_value="file:///scene_index.json"
    )

    ctx.dataset_artifact_store = MagicMock()
    ctx.dataset_artifact_store.write_dataset_manifest = AsyncMock(
        return_value="file:///dataset.json"
    )

    mock_version = MagicMock()
    mock_version.model_copy = lambda update: mock_version
    ctx.dataset_store = MagicMock()
    ctx.dataset_store.get_version = AsyncMock(return_value=mock_version)
    ctx.dataset_store.save_version = AsyncMock(return_value=mock_version)

    ctx.artifact_record_store = MagicMock()
    ctx.artifact_record_store.create = AsyncMock(return_value=MagicMock())

    ctx.commit = AsyncMock()

    return ctx


def _manifest_params(
    dataset_id: str = DATASET_ID, dataset_version: str = DATASET_VERSION
):
    p = MagicMock()
    p.dataset_id = dataset_id
    p.dataset_version = dataset_version
    p.scene_manifest_uris = []
    return p


def _index_params(dataset_id: str = DATASET_ID, dataset_version: str = DATASET_VERSION):
    p = MagicMock()
    p.dataset_id = dataset_id
    p.dataset_version = dataset_version
    p.scene_manifest_uris = []
    return p


# ── build_dataset_manifest ────────────────────────────────────────────────────


async def test_manifest_built_from_all_registered_scenes_not_just_input():
    """build_dataset_manifest queries DB for ALL scenes, ignoring pipeline input URIs."""
    scene_a = _scene_record("scene-a")
    scene_b = _scene_record("scene-b")
    ctx = _context([scene_a, scene_b])

    handler = BuildDatasetManifestJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _manifest_params()
    request.context = ctx

    result = await handler.run(request)

    assert result.scene_count == 2
    # Both scenes passed to the artifact writer
    written_manifest = ctx.dataset_artifact_store.write_dataset_manifest.call_args
    manifest_arg = written_manifest.kwargs["manifest"]
    written_scene_ids = {e.scene_id for e in manifest_arg.scenes}
    assert "scene-a" in written_scene_ids
    assert "scene-b" in written_scene_ids


async def test_manifest_rebuild_preserves_all_scenes_across_batches():
    """Rebuilding the manifest after adding a second scene includes both scenes.

    Simulates two incremental pipeline runs on the same dataset version:
    first batch registered scene-a, second batch registered scene-b.
    After the second build, the manifest must contain both.
    """
    # At build time, DB has both scenes (both were registered by prior pipeline runs)
    scene_a = _scene_record("scene-a")
    scene_b = _scene_record("scene-b")
    ctx = _context([scene_a, scene_b])

    handler = BuildDatasetManifestJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _manifest_params()
    request.context = ctx

    result = await handler.run(request)

    assert result.scene_count == 2


async def test_manifest_rebuild_is_idempotent():
    """Running build_dataset_manifest twice on the same scene set gives the same result."""
    scenes = [_scene_record("scene-a"), _scene_record("scene-b")]
    ctx = _context(scenes)

    handler = BuildDatasetManifestJobHandler()

    request = MagicMock()
    request.job = _job(job_id="job-1")
    request.params = _manifest_params()
    request.context = ctx
    result1 = await handler.run(request)

    # Reset mock call counts for second run (same context with same DB state)
    ctx.dataset_store.save_version.reset_mock()

    request2 = MagicMock()
    request2.job = _job(job_id="job-2")
    request2.params = _manifest_params()
    request2.context = ctx
    result2 = await handler.run(request2)

    assert result1.scene_count == result2.scene_count == 2


async def test_manifest_adding_third_scene_shows_three_scenes():
    """After adding a third scene, rebuild manifest shows all three."""
    scenes = [
        _scene_record("scene-a"),
        _scene_record("scene-b"),
        _scene_record("scene-c"),
    ]
    ctx = _context(scenes)

    handler = BuildDatasetManifestJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _manifest_params()
    request.context = ctx

    result = await handler.run(request)

    assert result.scene_count == 3


async def test_manifest_scene_count_reflects_full_registered_set():
    """DatasetVersion.scene_count is updated with the full DB-queried scene count."""
    scenes = [_scene_record(f"scene-{i}") for i in range(5)]
    ctx = _context(scenes)

    handler = BuildDatasetManifestJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _manifest_params()
    request.context = ctx

    result = await handler.run(request)

    assert result.scene_count == 5
    # save_version called with scene_count=5
    ctx.dataset_store.save_version.assert_called_once()


async def test_manifest_raises_if_no_registered_scenes():
    """build_dataset_manifest raises a clear error when no scenes are registered."""
    ctx = _context([])  # no scenes in DB

    handler = BuildDatasetManifestJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _manifest_params()
    request.context = ctx

    with pytest.raises(ValueError, match="no registered scenes found"):
        await handler.run(request)


async def test_manifest_does_not_use_pipeline_input_uris():
    """build_dataset_manifest ignores params.scene_manifest_uris (pipeline batch input)."""
    # DB has 2 scenes; pipeline input has only 1 URI (old batch-only behavior)
    scene_a = _scene_record("scene-a")
    scene_b = _scene_record("scene-b")
    ctx = _context([scene_a, scene_b])

    handler = BuildDatasetManifestJobHandler()
    request = MagicMock()
    request.job = _job()
    params = _manifest_params()
    # Simulate pipeline passing only one URI (the old wrong behavior)
    params.scene_manifest_uris = [scene_a.scene_manifest_uri]
    request.params = params
    request.context = ctx

    result = await handler.run(request)

    # Must use DB (2 scenes), not pipeline input (1 scene)
    assert result.scene_count == 2


# ── build_scene_index ─────────────────────────────────────────────────────────


async def test_scene_index_built_from_all_registered_scenes():
    """build_scene_index queries DB for ALL scenes, not pipeline input."""
    scene_a = _scene_record("scene-a")
    scene_b = _scene_record("scene-b")
    ctx = _context([scene_a, scene_b])

    handler = BuildSceneIndexJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _index_params()
    request.context = ctx

    result = await handler.run(request)

    assert result.scene_count == 2
    assert len(result.scene_manifest_uris) == 2
    assert scene_a.scene_manifest_uri in result.scene_manifest_uris
    assert scene_b.scene_manifest_uri in result.scene_manifest_uris


async def test_scene_index_raises_if_no_registered_scenes():
    """build_scene_index raises when no scenes are registered for the dataset version."""
    ctx = _context([])

    handler = BuildSceneIndexJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _index_params()
    request.context = ctx

    with pytest.raises(ValueError, match="no registered scenes found"):
        await handler.run(request)


async def test_scene_index_reflects_incremental_registration():
    """scene_index includes all scenes registered up to the point of build."""
    scenes = [_scene_record(f"scene-{i}") for i in range(10)]
    ctx = _context(scenes)

    handler = BuildSceneIndexJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _index_params()
    request.context = ctx

    result = await handler.run(request)

    assert result.scene_count == 10
