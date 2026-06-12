"""Unit tests for scene quality builder.

Tests pure builder functions in app.domains.scenes.quality — no DB, no async.

Covers:
- ready GT scene + passing validation → readiness=ready, selectable=true
- non-GT scene (has_ground_truth=False) → selectable=false, missing_ground_truth
- zero annotation_count on SceneRecord → selectable=false, missing_ground_truth
- scene quality uses SceneRecord GT fields even when profile run is missing/null
- blocking validation → readiness=blocked, reason validation_blocked
- warning validation → readiness=warning, selectable=false
- no validation run → readiness=unknown, reason validation_missing
- scene status not validated/profiled → readiness=unknown, reason scene_not_ready
- profile summary includes observed_channels and annotation_count
- validation summary fields pass through correctly
- ground_truth_source exposed in ground_truth summary
- register_scene/_build_scene_record_from_manifest copies GT fields from SceneManifest
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.scenes.quality import build_scene_quality, compute_scene_readiness
from app.domains.scenes.schemas import SceneQualityReadiness


# ── helpers ───────────────────────────────────────────────────────────────────


def _scene(
    status: SceneStatus = SceneStatus.PROFILED,
    sample_count: int = 40,
    frame_count: int = 80,
    annotation_count: int = 582,
    has_ground_truth: bool = True,
    ground_truth_source: str | None = "nuscenes",
) -> SceneRecord:
    return SceneRecord(
        scene_id="scene-001",
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        status=status,
        sample_count=sample_count,
        frame_count=frame_count,
        annotation_count=annotation_count,
        has_ground_truth=has_ground_truth,
        ground_truth_source=ground_truth_source,
    )


def _no_gt_scene(**kwargs) -> SceneRecord:
    return _scene(
        annotation_count=0, has_ground_truth=False, ground_truth_source=None, **kwargs
    )


def _validation_run(
    validation_status: str = "ready",
    should_block_pipeline: bool = False,
    error_count: int = 0,
    warning_count: int = 0,
    issue_count: int = 0,
) -> SceneValidationRunRecord:
    return SceneValidationRunRecord(
        run_id="val-001",
        status=RunStatus.SUCCEEDED,
        scene_id="scene-001",
        validation_status=validation_status,
        should_block_pipeline=should_block_pipeline,
        error_count=error_count,
        warning_count=warning_count,
        issue_count=issue_count,
        checked_sample_count=40,
        checked_frame_count=80,
    )


def _profile_run(
    annotation_count: int = 582,
    observed_channels: list[str] | None = None,
    sample_count: int = 40,
    frame_count: int = 80,
) -> SceneProfileRunRecord:
    return SceneProfileRunRecord(
        run_id="prof-001",
        status=RunStatus.SUCCEEDED,
        scene_id="scene-001",
        sample_count=sample_count,
        frame_count=frame_count,
        annotation_count=annotation_count,
        observed_channels=observed_channels or ["CAM_FRONT", "LIDAR_TOP"],
    )


# ── readiness: ready ──────────────────────────────────────────────────────────


def test_profiled_scene_with_ready_validation_is_ready():
    result = build_scene_quality(
        scene=_scene(status=SceneStatus.PROFILED),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=_profile_run(),
    )
    assert result.readiness == SceneQualityReadiness.READY


def test_validated_scene_status_also_accepted():
    result = build_scene_quality(
        scene=_scene(status=SceneStatus.VALIDATED),
        validation_run=_validation_run(validation_status="ready"),
    )
    assert result.readiness == SceneQualityReadiness.READY


# ── readiness: warning ────────────────────────────────────────────────────────


def test_warning_validation_status_produces_warning_readiness():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=_validation_run(validation_status="warning", warning_count=2),
        profile_run=_profile_run(),
    )
    assert result.readiness == SceneQualityReadiness.WARNING


# ── readiness: blocked ────────────────────────────────────────────────────────


def test_should_block_pipeline_produces_blocked():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=_validation_run(
            validation_status="ready", should_block_pipeline=True
        ),
        profile_run=_profile_run(),
    )
    assert result.readiness == SceneQualityReadiness.BLOCKED


def test_failed_validation_status_produces_blocked():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=_validation_run(validation_status="failed"),
    )
    assert result.readiness == SceneQualityReadiness.BLOCKED


def test_error_validation_status_produces_blocked():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=_validation_run(validation_status="error"),
    )
    assert result.readiness == SceneQualityReadiness.BLOCKED


# ── readiness: unknown ────────────────────────────────────────────────────────


def test_missing_validation_produces_unknown():
    result = build_scene_quality(scene=_scene(), validation_run=None)
    assert result.readiness == SceneQualityReadiness.UNKNOWN


def test_built_scene_status_produces_unknown():
    result = build_scene_quality(
        scene=_scene(status=SceneStatus.BUILT),
        validation_run=_validation_run(validation_status="ready"),
    )
    assert result.readiness == SceneQualityReadiness.UNKNOWN


def test_created_scene_status_produces_unknown():
    result = build_scene_quality(
        scene=_scene(status=SceneStatus.CREATED),
        validation_run=_validation_run(),
    )
    assert result.readiness == SceneQualityReadiness.UNKNOWN


# ── compute_scene_readiness standalone ───────────────────────────────────────


def test_compute_readiness_consistent_with_builder():
    scene = _scene()
    val = _validation_run(validation_status="warning")
    direct = compute_scene_readiness(scene, val)
    via_builder = build_scene_quality(scene=scene, validation_run=val).readiness
    assert direct == via_builder


# ── selectability: selectable ─────────────────────────────────────────────────


def test_ready_gt_scene_with_passing_validation_is_selectable():
    result = build_scene_quality(
        scene=_scene(has_ground_truth=True, annotation_count=50),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=_profile_run(annotation_count=50),
    )
    assert result.selectable_for_detection is True
    assert result.exclusion_reasons == []


def test_ready_gt_scene_selectable_even_without_profile_run():
    """SceneRecord GT fields are authoritative — profile run is optional."""
    result = build_scene_quality(
        scene=_scene(has_ground_truth=True, annotation_count=50),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=None,
    )
    assert result.selectable_for_detection is True
    assert result.exclusion_reasons == []


# ── selectability: not selectable ─────────────────────────────────────────────


def test_no_gt_flag_produces_missing_ground_truth_reason():
    """has_ground_truth=False on SceneRecord → not selectable regardless of profile."""
    result = build_scene_quality(
        scene=_no_gt_scene(),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=_profile_run(annotation_count=0),
    )
    assert result.selectable_for_detection is False
    assert "missing_ground_truth" in result.exclusion_reasons


def test_zero_scene_annotation_count_produces_missing_ground_truth():
    result = build_scene_quality(
        scene=_scene(has_ground_truth=False, annotation_count=0),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=None,
    )
    assert result.selectable_for_detection is False
    assert "missing_ground_truth" in result.exclusion_reasons


def test_non_gt_scene_selectable_false_even_when_profile_run_has_annotations():
    """Profile run annotation_count is not used for selectability — SceneRecord is authoritative."""
    result = build_scene_quality(
        scene=_no_gt_scene(),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=_profile_run(annotation_count=100),  # profile says 100 annotations
    )
    assert result.selectable_for_detection is False
    assert "missing_ground_truth" in result.exclusion_reasons


def test_blocking_validation_produces_validation_blocked_reason():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=_validation_run(
            validation_status="ready", should_block_pipeline=True
        ),
        profile_run=_profile_run(annotation_count=50),
    )
    assert result.selectable_for_detection is False
    assert "validation_blocked" in result.exclusion_reasons


def test_missing_validation_produces_validation_missing_reason():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=None,
        profile_run=_profile_run(annotation_count=50),
    )
    assert result.selectable_for_detection is False
    assert "validation_missing" in result.exclusion_reasons


def test_scene_not_profiled_produces_scene_not_ready_reason():
    result = build_scene_quality(
        scene=_scene(status=SceneStatus.BUILT),
        validation_run=_validation_run(validation_status="ready"),
        profile_run=_profile_run(annotation_count=50),
    )
    assert result.selectable_for_detection is False
    assert "scene_not_ready" in result.exclusion_reasons


# ── ground truth summary from SceneRecord ────────────────────────────────────


def test_gt_summary_uses_scene_record_fields():
    result = build_scene_quality(
        scene=_scene(
            has_ground_truth=True,
            annotation_count=582,
            ground_truth_source="nuscenes",
        ),
        profile_run=None,
    )
    assert result.ground_truth.has_ground_truth is True
    assert result.ground_truth.annotation_count == 582
    assert result.ground_truth.ground_truth_source == "nuscenes"


def test_gt_summary_false_for_raw_log_scene():
    result = build_scene_quality(
        scene=_no_gt_scene(),
    )
    assert result.ground_truth.has_ground_truth is False
    assert result.ground_truth.annotation_count == 0
    assert result.ground_truth.ground_truth_source is None


def test_gt_summary_populated_even_without_profile_run():
    """GT fields come from SceneRecord, not the profile run."""
    result = build_scene_quality(
        scene=_scene(
            has_ground_truth=True, annotation_count=100, ground_truth_source="nuscenes"
        ),
        validation_run=None,
        profile_run=None,
    )
    assert result.ground_truth.has_ground_truth is True
    assert result.ground_truth.annotation_count == 100


# ── validation summary fields ─────────────────────────────────────────────────


def test_validation_summary_fields_pass_through():
    result = build_scene_quality(
        scene=_scene(),
        validation_run=_validation_run(
            validation_status="warning",
            should_block_pipeline=False,
            error_count=0,
            warning_count=2,
            issue_count=2,
        ),
    )
    assert result.validation is not None
    assert result.validation.run_id == "val-001"
    assert result.validation.validation_status == "warning"
    assert result.validation.should_block_pipeline is False
    assert result.validation.blocking_issue_count == 0
    assert result.validation.warning_count == 2
    assert result.validation.issue_count == 2
    assert result.validation.checked_sample_count == 40
    assert result.validation.checked_frame_count == 80


def test_missing_validation_produces_none_section():
    result = build_scene_quality(scene=_scene(), validation_run=None)
    assert result.validation is None


# ── profile summary fields ────────────────────────────────────────────────────


def test_profile_summary_channels_and_annotation_pass_through():
    result = build_scene_quality(
        scene=_scene(),
        profile_run=_profile_run(
            annotation_count=582,
            observed_channels=["CAM_FRONT", "LIDAR_TOP"],
        ),
    )
    assert result.profile is not None
    assert result.profile.run_id == "prof-001"
    assert result.profile.annotation_count == 582
    assert "CAM_FRONT" in result.profile.observed_channels
    assert "LIDAR_TOP" in result.profile.observed_channels


def test_missing_profile_produces_none_section():
    result = build_scene_quality(scene=_scene(), profile_run=None)
    assert result.profile is None


# ── counts ────────────────────────────────────────────────────────────────────


def test_counts_use_scene_record_fields():
    result = build_scene_quality(
        scene=_scene(sample_count=40, frame_count=80, annotation_count=200),
    )
    assert result.counts.sample_count == 40
    assert result.counts.frame_count == 80
    assert result.counts.annotation_count == 200


# ── identity fields ───────────────────────────────────────────────────────────


def test_response_identity_fields():
    result = build_scene_quality(scene=_scene())
    assert result.scene_id == "scene-001"
    assert result.dataset_id == "nuscenes"
    assert result.dataset_version == "v1.0-mini"
    assert result.status == "profiled"


# ── SceneRecord GT field propagation via _build_scene_record_from_manifest ────
# The scene builder that copies GT fields from SceneManifest is
# _build_scene_record_from_manifest in register_scene.py (not nuscenes_scene.py).


def test_build_scene_record_from_manifest_copies_gt_fields():
    from sceneops_worker.jobs.dataset.register_scene import (
        _build_scene_record_from_manifest,
    )

    manifest = MagicMock()
    manifest.scene_id = "scene-001"
    manifest.sample_count = 40
    manifest.frame_count = 80
    manifest.annotation_count = 582
    manifest.has_ground_truth = True
    manifest.ground_truth_source = "nuscenes"
    manifest.channels = ["CAM_FRONT", "LIDAR_TOP"]
    manifest.metadata = {}

    params = MagicMock()
    params.origin_type = "real"
    params.generation_method = "unknown"

    record = _build_scene_record_from_manifest(
        scene_id="scene-001",
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        manifest_uri="file:///scenes/scene-001/manifest.json",
        manifest=manifest,
        params=params,
    )

    assert record.annotation_count == 582
    assert record.has_ground_truth is True
    assert record.ground_truth_source == "nuscenes"


def test_build_scene_record_from_manifest_raw_log_gt_defaults():
    from sceneops_worker.jobs.dataset.register_scene import (
        _build_scene_record_from_manifest,
    )

    manifest = MagicMock()
    manifest.scene_id = "scene-002"
    manifest.sample_count = 20
    manifest.frame_count = 40
    manifest.annotation_count = 0
    manifest.has_ground_truth = False
    manifest.ground_truth_source = None
    manifest.channels = ["CAM_FRONT", "LIDAR_TOP"]
    manifest.metadata = {}

    params = MagicMock()
    params.origin_type = "real"
    params.generation_method = "unknown"

    record = _build_scene_record_from_manifest(
        scene_id="scene-002",
        dataset_id="raw-log-dataset",
        dataset_version="v1",
        manifest_uri="file:///scenes/scene-002/manifest.json",
        manifest=manifest,
        params=params,
    )

    assert record.annotation_count == 0
    assert record.has_ground_truth is False
    assert record.ground_truth_source is None
