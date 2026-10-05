"""Unit tests for the scene-aggregate dataset quality builder.

Tests the pure functions in app.domains.datasets.quality directly --
no DB, no FastAPI, no async.

Both GET /datasets/{id}/versions/{v}/quality and
GET /datasets/{id}/versions/{v}/scenes/quality share the same aggregate
source (build_dataset_scene_quality_aggregate), so this tests that the
compact summary view is consistent with the detailed list view.

Dataset quality is canonical Scene quality only: validation readiness,
observed channels and counts. Labels and detection selectability are
derived label-set / sample-view concerns (ADR-007 §33).

Covers:
- Readiness: ready when every scene is ready
- Readiness: warning when any scene is warning/blocked/unknown
- Readiness: blocked when every scene is blocked
- Readiness: unknown when there are no scenes or all scenes are unknown
- Observed channels from the profile aggregate
- Counts summed from scenes
- Dataset quality and scene quality aggregate are consistent
"""

from __future__ import annotations

from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord

from app.domains.datasets.quality import (
    build_dataset_version_quality_from_aggregate,
    compute_dataset_readiness_from_aggregate,
)
from app.domains.datasets.schemas import (
    DatasetQualityReadiness,
    DatasetSceneQualityAggregateSummary,
)


def _version(
    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED,
) -> DatasetVersionRecord:
    return DatasetVersionRecord(dataset_id="d", version="v1", status=status)


def _summary(
    scene_count: int = 10,
    ready_scene_count: int = 10,
    warning_scene_count: int = 0,
    blocked_scene_count: int = 0,
    unknown_scene_count: int = 0,
    total_keyframe_count: int = 0,
    total_observation_count: int = 808,
    observed_channels: list[str] | None = None,
) -> DatasetSceneQualityAggregateSummary:
    return DatasetSceneQualityAggregateSummary(
        scene_count=scene_count,
        ready_scene_count=ready_scene_count,
        warning_scene_count=warning_scene_count,
        blocked_scene_count=blocked_scene_count,
        unknown_scene_count=unknown_scene_count,
        total_keyframe_count=total_keyframe_count,
        total_observation_count=total_observation_count,
        observed_channels=observed_channels or ["CAM_FRONT", "LIDAR_TOP"],
    )


def test_ready_when_every_scene_is_ready():
    assert (
        compute_dataset_readiness_from_aggregate(_summary())
        == DatasetQualityReadiness.READY
    )


def test_warning_when_any_scene_is_warning_blocked_or_unknown():
    for kwargs in (
        {"ready_scene_count": 8, "warning_scene_count": 2},
        {"ready_scene_count": 9, "blocked_scene_count": 1},
        {"ready_scene_count": 9, "unknown_scene_count": 1},
    ):
        assert (
            compute_dataset_readiness_from_aggregate(_summary(**kwargs))
            == DatasetQualityReadiness.WARNING
        )


def test_blocked_only_when_every_scene_is_blocked():
    summary = _summary(scene_count=4, ready_scene_count=0, blocked_scene_count=4)
    assert (
        compute_dataset_readiness_from_aggregate(summary)
        == DatasetQualityReadiness.BLOCKED
    )


def test_unknown_when_empty_or_nothing_validated():
    assert (
        compute_dataset_readiness_from_aggregate(
            _summary(scene_count=0, ready_scene_count=0)
        )
        == DatasetQualityReadiness.UNKNOWN
    )
    assert (
        compute_dataset_readiness_from_aggregate(
            _summary(scene_count=3, ready_scene_count=0, unknown_scene_count=3)
        )
        == DatasetQualityReadiness.UNKNOWN
    )


def test_a_recording_dataset_is_not_blocked_for_lacking_embedded_ground_truth():
    # Every scene validated ready, none carries embedded annotations:
    # readiness is about Scene quality, not labels.
    assert (
        compute_dataset_readiness_from_aggregate(
            _summary(scene_count=6, ready_scene_count=6)
        )
        == DatasetQualityReadiness.READY
    )


def test_response_identity_counts_and_channels():
    summary = _summary(
        scene_count=3,
        ready_scene_count=2,
        warning_scene_count=1,
        total_keyframe_count=7,
        total_observation_count=90,
        observed_channels=["CAM_FRONT"],
    )
    response = build_dataset_version_quality_from_aggregate(_version(), summary)
    assert (response.dataset_id, response.version, response.status) == (
        "d",
        "v1",
        "registered",
    )
    assert response.counts.scene_count == 3
    assert response.counts.keyframe_count == 7
    assert response.counts.observation_count == 90
    assert response.scene_quality.warning_scene_count == 1
    assert response.profile.observed_channels == ["CAM_FRONT"]
    dumped = response.model_dump()
    assert "ground_truth" not in dumped
    assert "manifest_uri" not in dumped


def test_compact_view_mirrors_the_aggregate_buckets():
    summary = _summary(
        scene_count=5,
        ready_scene_count=2,
        warning_scene_count=1,
        blocked_scene_count=1,
        unknown_scene_count=1,
    )
    response = build_dataset_version_quality_from_aggregate(_version(), summary)
    assert response.validation.ready_scene_count == summary.ready_scene_count
    assert response.validation.blocked_scene_count == summary.blocked_scene_count
    assert response.scene_quality.unknown_scene_count == summary.unknown_scene_count
