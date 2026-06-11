"""Unit tests for the scene-aggregate dataset quality builder.

Tests the pure functions in app.domains.datasets.quality directly —
no DB, no FastAPI, no async.

Both GET /datasets/{id}/versions/{v}/quality and
GET /datasets/{id}/versions/{v}/scenes/quality share the same aggregate
source (build_dataset_scene_quality_aggregate), so this tests that the
compact summary view is consistent with the detailed list view.

Covers:
- Readiness: ready when all scenes ready/selectable, no blocked/unknown
- Readiness: warning when some scenes excluded but ≥1 selectable
- Readiness: blocked when no scenes selectable
- Readiness: unknown when no scenes or all scenes unknown
- GT summary uses scene aggregate GT fields
- Observed channels from profile aggregate
- Exclusion reason counts from scene aggregate
- Counts (sample/frame/annotation) summed from scenes
- Coverage ratio computed from GT/scene counts
- Dataset quality and scene quality aggregate are consistent
"""

from __future__ import annotations

from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.datasets.schemas.enums import DatasetVersionStatus

from app.domains.datasets.quality import (
    build_dataset_version_quality_from_aggregate,
    compute_dataset_readiness_from_aggregate,
)
from app.domains.datasets.schemas import (
    DatasetQualityReadiness,
    DatasetSceneQualityAggregateSummary,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _version(
    status: DatasetVersionStatus = DatasetVersionStatus.READY,
    manifest_uri: str | None = "file:///dataset.json",
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id="nuscenes",
        version="v1.0-mini",
        status=status,
        manifest_uri=manifest_uri,
    )


def _summary(
    scene_count: int = 10,
    ready_scene_count: int = 10,
    warning_scene_count: int = 0,
    blocked_scene_count: int = 0,
    unknown_scene_count: int = 0,
    selectable_for_detection_count: int = 10,
    non_selectable_for_detection_count: int = 0,
    ground_truth_scene_count: int = 10,
    annotated_scene_count: int = 10,
    total_sample_count: int = 404,
    total_frame_count: int = 808,
    total_annotation_count: int = 14982,
    exclusion_reason_counts: dict | None = None,
    observed_channels: list[str] | None = None,
) -> DatasetSceneQualityAggregateSummary:
    return DatasetSceneQualityAggregateSummary(
        scene_count=scene_count,
        ready_scene_count=ready_scene_count,
        warning_scene_count=warning_scene_count,
        blocked_scene_count=blocked_scene_count,
        unknown_scene_count=unknown_scene_count,
        selectable_for_detection_count=selectable_for_detection_count,
        non_selectable_for_detection_count=non_selectable_for_detection_count,
        ground_truth_scene_count=ground_truth_scene_count,
        annotated_scene_count=annotated_scene_count,
        total_sample_count=total_sample_count,
        total_frame_count=total_frame_count,
        total_annotation_count=total_annotation_count,
        exclusion_reason_counts=exclusion_reason_counts or {},
        observed_channels=observed_channels or ["CAM_FRONT", "LIDAR_TOP"],
    )


# ── readiness: ready ──────────────────────────────────────────────────────────


def test_ready_when_all_scenes_selectable_and_clean():
    summary = _summary(
        scene_count=10,
        ready_scene_count=10,
        selectable_for_detection_count=10,
        non_selectable_for_detection_count=0,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.READY
    )


def test_ready_requires_zero_blocked_and_unknown():
    summary = _summary(
        scene_count=5,
        ready_scene_count=5,
        blocked_scene_count=0,
        unknown_scene_count=0,
        selectable_for_detection_count=5,
        non_selectable_for_detection_count=0,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.READY
    )


# ── readiness: warning ────────────────────────────────────────────────────────


def test_warning_when_some_scenes_not_selectable():
    summary = _summary(
        scene_count=10,
        ready_scene_count=8,
        warning_scene_count=2,
        selectable_for_detection_count=8,
        non_selectable_for_detection_count=2,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.WARNING
    )


def test_warning_when_some_scenes_blocked_but_others_selectable():
    summary = _summary(
        scene_count=10,
        ready_scene_count=7,
        blocked_scene_count=3,
        selectable_for_detection_count=7,
        non_selectable_for_detection_count=3,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.WARNING
    )


def test_warning_when_unknown_scenes_present_but_selectable_exist():
    summary = _summary(
        scene_count=10,
        ready_scene_count=8,
        unknown_scene_count=2,
        selectable_for_detection_count=8,
        non_selectable_for_detection_count=2,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.WARNING
    )


# ── readiness: blocked ────────────────────────────────────────────────────────


def test_blocked_when_no_selectable_scenes():
    summary = _summary(
        scene_count=5,
        blocked_scene_count=5,
        selectable_for_detection_count=0,
        non_selectable_for_detection_count=5,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.BLOCKED
    )


def test_blocked_when_scenes_validated_but_none_selectable():
    """BLOCKED: validation has run, some scenes have validation results, but none are selectable."""
    summary = _summary(
        scene_count=5,
        ready_scene_count=0,
        blocked_scene_count=3,
        warning_scene_count=2,
        unknown_scene_count=0,
        selectable_for_detection_count=0,
        non_selectable_for_detection_count=5,
        ground_truth_scene_count=0,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.BLOCKED
    )


def test_unknown_when_all_scenes_missing_gt_and_no_validation():
    """When all scenes have no validation data (all unknown), readiness is UNKNOWN."""
    summary = _summary(
        scene_count=3,
        unknown_scene_count=3,
        ready_scene_count=0,
        selectable_for_detection_count=0,
        non_selectable_for_detection_count=3,
        ground_truth_scene_count=0,
    )
    # All unknown → no validation data → UNKNOWN, not BLOCKED
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.UNKNOWN
    )


# ── readiness: unknown ────────────────────────────────────────────────────────


def test_unknown_when_no_scenes():
    summary = _summary(scene_count=0)
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.UNKNOWN
    )


def test_unknown_when_all_scenes_unknown():
    summary = _summary(
        scene_count=5,
        unknown_scene_count=5,
        ready_scene_count=0,
        selectable_for_detection_count=0,
    )
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.UNKNOWN
    )


# ── ground truth summary ──────────────────────────────────────────────────────


def test_ground_truth_summary_has_ground_truth_true_when_gt_scenes_exist():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(ground_truth_scene_count=10, scene_count=10),
    )
    assert result.ground_truth.has_ground_truth is True
    assert result.ground_truth.ground_truth_scene_count == 10


def test_ground_truth_summary_false_when_no_gt_scenes():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(ground_truth_scene_count=0),
    )
    assert result.ground_truth.has_ground_truth is False


def test_ground_truth_coverage_ratio_is_correct():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(scene_count=10, ground_truth_scene_count=8),
    )
    assert result.ground_truth.ground_truth_coverage_ratio == 0.8


def test_ground_truth_annotation_count_from_aggregate():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(total_annotation_count=14982),
    )
    assert result.ground_truth.annotation_count == 14982


# ── observed channels ─────────────────────────────────────────────────────────


def test_observed_channels_from_scene_profile_aggregate():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(observed_channels=["CAM_FRONT", "LIDAR_TOP"]),
    )
    assert "CAM_FRONT" in result.scene_quality.observed_channels
    assert "LIDAR_TOP" in result.scene_quality.observed_channels
    assert result.profile.observed_channels == result.scene_quality.observed_channels


# ── exclusion reason counts ───────────────────────────────────────────────────


def test_exclusion_reason_counts_from_scene_aggregate():
    reasons = {"missing_ground_truth": 2, "validation_blocked": 1}
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(exclusion_reason_counts=reasons),
    )
    assert result.scene_quality.exclusion_reason_counts["missing_ground_truth"] == 2
    assert result.scene_quality.exclusion_reason_counts["validation_blocked"] == 1


# ── counts ────────────────────────────────────────────────────────────────────


def test_counts_from_scene_aggregate_totals():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(
            scene_count=10,
            total_sample_count=404,
            total_frame_count=808,
            total_annotation_count=14982,
            ground_truth_scene_count=10,
            selectable_for_detection_count=10,
        ),
    )
    assert result.counts.scene_count == 10
    assert result.counts.sample_count == 404
    assert result.counts.frame_count == 808
    assert result.counts.annotation_count == 14982
    assert result.counts.ground_truth_scene_count == 10
    assert result.counts.selectable_scene_count == 10


# ── validation summary ────────────────────────────────────────────────────────


def test_validation_summary_uses_scene_readiness_buckets():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(
            scene_count=10,
            ready_scene_count=8,
            warning_scene_count=1,
            blocked_scene_count=1,
        ),
    )
    assert result.validation.ready_scene_count == 8
    assert result.validation.warning_scene_count == 1
    assert result.validation.blocked_scene_count == 1
    assert result.validation.unknown_scene_count == 0


# ── identity fields ───────────────────────────────────────────────────────────


def test_response_identity_fields():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(),
        summary=_summary(),
    )
    assert result.dataset_id == "nuscenes"
    assert result.version == "v1.0-mini"
    assert result.status == "ready"


def test_manifest_uri_from_version_record():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(manifest_uri="file:///manifests/dataset.json"),
        summary=_summary(),
    )
    assert result.manifest_uri == "file:///manifests/dataset.json"


def test_ingesting_version_status_reflected():
    result = build_dataset_version_quality_from_aggregate(
        version=_version(status=DatasetVersionStatus.INGESTING),
        summary=_summary(scene_count=0),
    )
    assert result.status == "ingesting"
    assert result.readiness == DatasetQualityReadiness.UNKNOWN


# ── consistency: dataset quality == scene quality aggregate ───────────────────


def test_dataset_quality_readiness_consistent_with_scene_aggregate():
    """compute_dataset_readiness_from_aggregate is the single source of readiness."""
    summary = _summary(
        scene_count=10,
        ready_scene_count=10,
        selectable_for_detection_count=10,
    )
    direct = compute_dataset_readiness_from_aggregate(summary)
    via_builder = build_dataset_version_quality_from_aggregate(
        version=_version(), summary=summary
    ).readiness
    assert direct == via_builder


def test_dataset_quality_scene_counts_mirror_aggregate():
    """scene_quality section mirrors the aggregate summary exactly."""
    summary = _summary(
        scene_count=10,
        ready_scene_count=8,
        warning_scene_count=1,
        blocked_scene_count=1,
        selectable_for_detection_count=9,
        non_selectable_for_detection_count=1,
    )
    result = build_dataset_version_quality_from_aggregate(
        version=_version(), summary=summary
    )
    assert result.scene_quality.ready_scene_count == summary.ready_scene_count
    assert result.scene_quality.warning_scene_count == summary.warning_scene_count
    assert result.scene_quality.blocked_scene_count == summary.blocked_scene_count
    assert (
        result.scene_quality.selectable_for_detection_count
        == summary.selectable_for_detection_count
    )
