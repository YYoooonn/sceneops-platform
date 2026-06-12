"""Unit tests for dataset scene quality aggregate builder.

Tests the pure aggregate function in app.domains.datasets.quality — no DB,
no async, no FastAPI.

Covers:
- aggregate counts readiness buckets correctly
- selectable/non-selectable counts are correct
- ground_truth_scene_count and annotated_scene_count use SceneRecord fields
- total annotation/sample/frame counts are summed
- exclusion reason counts are aggregated
- observed channels union is deterministic (sorted)
- summary is global, scenes list is paginated
- empty dataset version returns zero summary
"""

from __future__ import annotations

from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.datasets.quality import build_dataset_scene_quality_aggregate
from app.domains.scenes.quality import build_scene_quality


# ── helpers ───────────────────────────────────────────────────────────────────


def _scene(
    scene_id: str = "scene-001",
    status: SceneStatus = SceneStatus.PROFILED,
    sample_count: int = 40,
    frame_count: int = 80,
    annotation_count: int = 582,
    has_ground_truth: bool = True,
    ground_truth_source: str | None = "nuscenes",
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        status=status,
        sample_count=sample_count,
        frame_count=frame_count,
        annotation_count=annotation_count,
        has_ground_truth=has_ground_truth,
        ground_truth_source=ground_truth_source,
    )


def _validation_run(
    scene_id: str = "scene-001",
    validation_status: str = "ready",
    should_block_pipeline: bool = False,
) -> SceneValidationRunRecord:
    return SceneValidationRunRecord(
        run_id=f"val-{scene_id}",
        status=RunStatus.SUCCEEDED,
        scene_id=scene_id,
        validation_status=validation_status,
        should_block_pipeline=should_block_pipeline,
        error_count=0,
        warning_count=0,
        issue_count=0,
        checked_sample_count=40,
        checked_frame_count=80,
    )


def _profile_run(
    scene_id: str = "scene-001",
    annotation_count: int = 582,
    observed_channels: list[str] | None = None,
) -> SceneProfileRunRecord:
    return SceneProfileRunRecord(
        run_id=f"prof-{scene_id}",
        status=RunStatus.SUCCEEDED,
        scene_id=scene_id,
        sample_count=40,
        frame_count=80,
        annotation_count=annotation_count,
        observed_channels=observed_channels or ["CAM_FRONT", "LIDAR_TOP"],
    )


def _quality(
    scene: SceneRecord,
    validation_run: SceneValidationRunRecord | None = None,
    profile_run: SceneProfileRunRecord | None = None,
):
    return build_scene_quality(
        scene=scene, validation_run=validation_run, profile_run=profile_run
    )


# ── empty dataset ─────────────────────────────────────────────────────────────


def test_empty_dataset_returns_zero_summary():
    summary = build_dataset_scene_quality_aggregate([])
    assert summary.scene_count == 0
    assert summary.ready_scene_count == 0
    assert summary.warning_scene_count == 0
    assert summary.blocked_scene_count == 0
    assert summary.unknown_scene_count == 0
    assert summary.selectable_for_detection_count == 0
    assert summary.non_selectable_for_detection_count == 0
    assert summary.ground_truth_scene_count == 0
    assert summary.annotated_scene_count == 0
    assert summary.total_sample_count == 0
    assert summary.total_frame_count == 0
    assert summary.total_annotation_count == 0
    assert summary.exclusion_reason_counts == {}
    assert summary.observed_channels == []


# ── readiness buckets ─────────────────────────────────────────────────────────


def test_readiness_buckets_are_counted_correctly():
    scenes = [
        _quality(_scene("s1"), _validation_run("s1", "ready")),  # ready
        _quality(_scene("s2"), _validation_run("s2", "warning")),  # warning
        _quality(
            _scene("s3"), _validation_run("s3", should_block_pipeline=True)
        ),  # blocked
        _quality(_scene("s4"), validation_run=None),  # unknown
    ]
    summary = build_dataset_scene_quality_aggregate(scenes)
    assert summary.scene_count == 4
    assert summary.ready_scene_count == 1
    assert summary.warning_scene_count == 1
    assert summary.blocked_scene_count == 1
    assert summary.unknown_scene_count == 1


def test_all_ready_scenes():
    scenes = [
        _quality(_scene(f"s{i}"), _validation_run(f"s{i}", "ready")) for i in range(5)
    ]
    summary = build_dataset_scene_quality_aggregate(scenes)
    assert summary.ready_scene_count == 5
    assert summary.warning_scene_count == 0
    assert summary.blocked_scene_count == 0
    assert summary.unknown_scene_count == 0


# ── selectable counts ─────────────────────────────────────────────────────────


def test_selectable_and_non_selectable_counts():
    selectable = _quality(
        _scene("s1", has_ground_truth=True, annotation_count=100),
        _validation_run("s1", "ready"),
    )
    not_selectable = _quality(
        _scene("s2", has_ground_truth=False, annotation_count=0),
        _validation_run("s2", "ready"),
    )
    summary = build_dataset_scene_quality_aggregate([selectable, not_selectable])
    assert summary.selectable_for_detection_count == 1
    assert summary.non_selectable_for_detection_count == 1


# ── GT counts ─────────────────────────────────────────────────────────────────


def test_ground_truth_scene_count_uses_has_ground_truth_flag():
    gt_scene = _quality(
        _scene("s1", has_ground_truth=True, annotation_count=100),
        _validation_run("s1"),
    )
    no_gt_scene = _quality(
        _scene("s2", has_ground_truth=False, annotation_count=0),
        _validation_run("s2"),
    )
    summary = build_dataset_scene_quality_aggregate([gt_scene, no_gt_scene])
    assert summary.ground_truth_scene_count == 1


def test_annotated_scene_count_uses_annotation_count_field():
    annotated = _quality(
        _scene("s1", annotation_count=50, has_ground_truth=True),
    )
    not_annotated = _quality(
        _scene("s2", annotation_count=0, has_ground_truth=False),
    )
    summary = build_dataset_scene_quality_aggregate([annotated, not_annotated])
    assert summary.annotated_scene_count == 1


# ── totals ────────────────────────────────────────────────────────────────────


def test_total_sample_frame_annotation_counts_are_summed():
    q1 = _quality(_scene("s1", sample_count=40, frame_count=80, annotation_count=582))
    q2 = _quality(_scene("s2", sample_count=30, frame_count=60, annotation_count=400))
    summary = build_dataset_scene_quality_aggregate([q1, q2])
    assert summary.total_sample_count == 70
    assert summary.total_frame_count == 140
    assert summary.total_annotation_count == 982


# ── exclusion reason counts ───────────────────────────────────────────────────


def test_exclusion_reason_counts_are_aggregated():
    # Both scenes missing GT
    q1 = _quality(
        _scene("s1", has_ground_truth=False, annotation_count=0),
        _validation_run("s1", "ready"),
    )
    # One scene also has validation blocked
    q2 = _quality(
        _scene("s2", has_ground_truth=False, annotation_count=0),
        _validation_run("s2", should_block_pipeline=True),
    )
    summary = build_dataset_scene_quality_aggregate([q1, q2])
    assert summary.exclusion_reason_counts["missing_ground_truth"] == 2
    assert summary.exclusion_reason_counts["validation_blocked"] == 1


def test_no_exclusion_reasons_when_all_selectable():
    q = _quality(
        _scene("s1", has_ground_truth=True, annotation_count=100),
        _validation_run("s1", "ready"),
    )
    summary = build_dataset_scene_quality_aggregate([q])
    assert summary.exclusion_reason_counts == {}


# ── observed channels ─────────────────────────────────────────────────────────


def test_observed_channels_union_is_sorted():
    q1 = _quality(
        _scene("s1"),
        _validation_run("s1"),
        _profile_run("s1", observed_channels=["LIDAR_TOP", "CAM_FRONT"]),
    )
    q2 = _quality(
        _scene("s2"),
        _validation_run("s2"),
        _profile_run("s2", observed_channels=["CAM_BACK", "CAM_FRONT"]),
    )
    summary = build_dataset_scene_quality_aggregate([q1, q2])
    assert summary.observed_channels == sorted(["CAM_BACK", "CAM_FRONT", "LIDAR_TOP"])


def test_observed_channels_empty_when_no_profile_runs():
    q = _quality(_scene("s1"), _validation_run("s1"), profile_run=None)
    summary = build_dataset_scene_quality_aggregate([q])
    assert summary.observed_channels == []


def test_observed_channels_deterministic_across_calls():
    scenes = [
        _quality(
            _scene(f"s{i}"),
            _validation_run(f"s{i}"),
            _profile_run(
                f"s{i}", observed_channels=["CAM_FRONT", "LIDAR_TOP", "CAM_BACK"]
            ),
        )
        for i in range(3)
    ]
    a = build_dataset_scene_quality_aggregate(scenes)
    b = build_dataset_scene_quality_aggregate(scenes)
    assert a.observed_channels == b.observed_channels


# ── summary is global, not page-scoped ───────────────────────────────────────


def test_summary_counts_all_scenes_not_just_page():
    all_quality = [
        _quality(
            _scene(f"s{i}", has_ground_truth=True, annotation_count=10),
            _validation_run(f"s{i}", "ready"),
        )
        for i in range(10)
    ]
    # Summary built over all 10
    summary = build_dataset_scene_quality_aggregate(all_quality)
    assert summary.scene_count == 10
    assert summary.ready_scene_count == 10
    assert summary.total_annotation_count == 100

    # Pagination is separate — first page of 3
    page = all_quality[:3]
    assert len(page) == 3

    # Summary still covers all 10, page covers 3
    page_summary = build_dataset_scene_quality_aggregate(page)
    assert page_summary.scene_count == 3  # page-scope summary would differ
    # Full summary is invariant to pagination
    assert summary.scene_count == 10
