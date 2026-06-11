"""Unit tests for select_detection_scenes.

Covers:
- ground_truth_only mode records selected and skipped scenes with correct structure
- skipped_scene entries include sample_count and reason
- selected_sample_count reflects total samples across selected scenes
- all mode selects everything
- explicit_scenes mode filters by scene_id list
- max_scenes cap applies
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sceneops_core.jobs.schemas.params.detection import (
    DetectionSceneSelectionConfig,
    DetectionSceneSelectionMode,
)
from sceneops_worker.scenes.selection import select_detection_scenes


# ── helpers ───────────────────────────────────────────────────────────────────


def _scene_entry(scene_id: str, manifest_uri: str | None = None) -> MagicMock:
    entry = MagicMock()
    entry.scene_id = scene_id
    entry.scene_manifest_uri = manifest_uri or f"file:///{scene_id}/manifest.json"
    return entry


def _scene_manifest(
    scene_id: str,
    annotation_count: int = 0,
    sample_count: int = 10,
    has_ground_truth: bool | None = None,
    ground_truth_source: str | None = None,
) -> MagicMock:
    manifest = MagicMock()
    manifest.scene_id = scene_id
    manifest.annotation_count = annotation_count
    manifest.sample_count = sample_count
    manifest.has_ground_truth = (
        has_ground_truth if has_ground_truth is not None else (annotation_count > 0)
    )
    manifest.ground_truth_source = ground_truth_source
    manifest.samples = []
    return manifest


def _dataset_manifest(scene_ids: list[str]) -> MagicMock:
    ds = MagicMock()
    ds.scenes = [_scene_entry(sid) for sid in scene_ids]
    return ds


def _store_from_manifests(manifests: dict[str, MagicMock | None]) -> MagicMock:
    """Build a mock SceneArtifactStore that returns manifests keyed by URI.

    URI format is ``file:///{scene_id}/manifest.json``.
    Split on "/" gives ``['file:', '', '', '{scene_id}', 'manifest.json']``,
    so index 3 is the scene_id.
    """
    store = MagicMock()

    async def load(uri: str) -> MagicMock | None:
        scene_id = uri.split("/")[3]
        return manifests.get(scene_id)

    store.load_scene_manifest = load
    return store


def _selection(
    mode: DetectionSceneSelectionMode = DetectionSceneSelectionMode.ALL,
    scene_ids: list[str] | None = None,
    max_scenes: int | None = None,
    min_annotation_count: int = 1,
    ground_truth_sources: list[str] | None = None,
) -> DetectionSceneSelectionConfig:
    return DetectionSceneSelectionConfig(
        mode=mode,
        scene_ids=scene_ids or [],
        max_scenes=max_scenes,
        min_annotation_count=min_annotation_count,
        ground_truth_sources=ground_truth_sources or [],
    )


# ── all mode ─────────────────────────────────────────────────────────────────


async def test_all_mode_selects_every_scene():
    manifests = {
        "scene-001": _scene_manifest("scene-001", sample_count=5),
        "scene-002": _scene_manifest("scene-002", sample_count=8),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001", "scene-002"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.ALL),
    )

    assert result["selected_scene_count"] == 2
    assert "scene-001" in result["selected_scene_ids"]
    assert "scene-002" in result["selected_scene_ids"]
    assert result["skipped_scene_count"] == 0
    assert result["selected_sample_count"] == 13


async def test_all_mode_selected_sample_count():
    manifests = {
        "scene-001": _scene_manifest("scene-001", sample_count=20),
        "scene-002": _scene_manifest("scene-002", sample_count=15),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001", "scene-002"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.ALL),
    )

    assert result["selected_sample_count"] == 35


# ── ground_truth_only mode ────────────────────────────────────────────────────


async def test_gt_only_selects_scenes_with_annotations():
    manifests = {
        "scene-gt": _scene_manifest("scene-gt", annotation_count=50, sample_count=20),
        "scene-no-gt": _scene_manifest(
            "scene-no-gt", annotation_count=0, sample_count=5
        ),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-gt", "scene-no-gt"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.GROUND_TRUTH_ONLY),
    )

    assert result["selected_scene_count"] == 1
    assert result["selected_scene_ids"] == ["scene-gt"]
    assert result["selected_sample_count"] == 20
    assert result["skipped_scene_count"] == 1


async def test_gt_only_skipped_scene_has_reason_and_sample_count():
    manifests = {
        "scene-gt": _scene_manifest("scene-gt", annotation_count=10, sample_count=20),
        "scene-no-gt": _scene_manifest(
            "scene-no-gt", annotation_count=0, sample_count=7
        ),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-gt", "scene-no-gt"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.GROUND_TRUTH_ONLY),
    )

    skipped = result["skipped_scenes"]
    assert len(skipped) == 1
    entry = skipped[0]
    assert entry["scene_id"] == "scene-no-gt"
    assert entry["reason"] == "scene_has_no_ground_truth"
    assert entry["sample_count"] == 7
    assert entry["annotation_count"] == 0
    assert entry["has_ground_truth"] is False


async def test_gt_only_selected_annotation_count():
    manifests = {
        "scene-a": _scene_manifest("scene-a", annotation_count=30, sample_count=10),
        "scene-b": _scene_manifest("scene-b", annotation_count=0, sample_count=5),
        "scene-c": _scene_manifest("scene-c", annotation_count=20, sample_count=8),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-a", "scene-b", "scene-c"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.GROUND_TRUTH_ONLY),
    )

    assert result["selected_annotation_count"] == 50
    assert result["selected_sample_count"] == 18
    assert result["selected_scene_count"] == 2


async def test_gt_only_scene_with_zero_annotations_skipped_includes_sample_count():
    """A no-GT scene skipped entry reports its sample_count for observability."""
    manifests = {
        "scene-no-gt": _scene_manifest(
            "scene-no-gt", annotation_count=0, sample_count=12
        ),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-no-gt"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.GROUND_TRUTH_ONLY),
    )

    assert result["skipped_scenes"][0]["sample_count"] == 12


# ── zero-annotation sample in a GT scene stays selected ──────────────────────


async def test_gt_scene_with_some_zero_annotation_samples_is_still_selected():
    """Scene has GT overall (annotation_count > 0) → selected even if some samples
    have no annotations. Those empty samples are valid negative samples.
    """
    manifests = {
        "scene-mixed": _scene_manifest(
            "scene-mixed", annotation_count=5, sample_count=10
        ),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-mixed"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.GROUND_TRUTH_ONLY),
    )

    assert result["selected_scene_count"] == 1
    assert result["selected_scene_ids"] == ["scene-mixed"]


# ── explicit_scenes mode ──────────────────────────────────────────────────────


async def test_explicit_scenes_mode_filters_by_scene_id_list():
    manifests = {
        "scene-001": _scene_manifest("scene-001", sample_count=5),
        "scene-002": _scene_manifest("scene-002", sample_count=8),
        "scene-003": _scene_manifest("scene-003", sample_count=3),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001", "scene-002", "scene-003"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(
            mode=DetectionSceneSelectionMode.EXPLICIT_SCENES,
            scene_ids=["scene-001", "scene-003"],
        ),
    )

    assert result["selected_scene_count"] == 2
    assert set(result["selected_scene_ids"]) == {"scene-001", "scene-003"}
    assert result["requested_scene_count"] == 2
    assert result["selected_sample_count"] == 8  # 5 + 3


# ── requested_scene_count ─────────────────────────────────────────────────────


async def test_requested_scene_count_zero_for_all_mode():
    manifests = {"scene-001": _scene_manifest("scene-001")}
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.ALL),
    )
    assert result["requested_scene_count"] == 0


async def test_requested_scene_count_reflects_explicit_list():
    manifests = {"scene-001": _scene_manifest("scene-001")}
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(
            mode=DetectionSceneSelectionMode.EXPLICIT_SCENES,
            scene_ids=["scene-001", "scene-002"],
        ),
    )
    assert result["requested_scene_count"] == 2


# ── max_scenes cap ─────────────────────────────────────────────────────────────


async def test_max_scenes_caps_selected_count():
    manifests = {
        "scene-001": _scene_manifest("scene-001", sample_count=5),
        "scene-002": _scene_manifest("scene-002", sample_count=5),
        "scene-003": _scene_manifest("scene-003", sample_count=5),
    }
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001", "scene-002", "scene-003"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(mode=DetectionSceneSelectionMode.ALL, max_scenes=2),
    )

    assert result["selected_scene_count"] == 2
    assert result["selected_sample_count"] == 10


# ── missing manifest ──────────────────────────────────────────────────────────


async def test_missing_manifest_scene_is_skipped_with_reason():
    store = MagicMock()
    store.load_scene_manifest = AsyncMock(return_value=None)

    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-missing"]),
        scene_artifact_store=store,
        selection=_selection(mode=DetectionSceneSelectionMode.ALL),
    )

    assert result["selected_scene_count"] == 0
    assert result["skipped_scenes"][0]["reason"] == "scene_manifest_not_found"


# ── summary field presence ────────────────────────────────────────────────────


async def test_result_contains_all_expected_keys():
    manifests = {"scene-001": _scene_manifest("scene-001")}
    result = await select_detection_scenes(
        dataset_manifest=_dataset_manifest(["scene-001"]),
        scene_artifact_store=_store_from_manifests(manifests),
        selection=_selection(),
    )

    expected_keys = {
        "mode",
        "requested_scene_count",
        "requested_scene_ids",
        "selected_scene_ids",
        "selected_scene_count",
        "selected_sample_count",
        "selected_annotation_count",
        "total_scene_count",
        "inspected_scene_count",
        "skipped_scene_count",
        "skipped_scenes",
        "max_scenes",
        "max_samples",
        "max_samples_per_scene",
        "min_annotation_count",
        "ground_truth_sources",
    }
    assert expected_keys.issubset(result.keys())
