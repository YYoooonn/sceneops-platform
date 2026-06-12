"""Scene quality response builder.

Pure functions — receive already-fetched records, return SceneQualityResponse.
Independently testable without a running service or DB.
"""

from __future__ import annotations

from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.scenes.schemas import (
    SceneGroundTruthQualitySummary,
    SceneProfileQualitySummary,
    SceneQualityCounts,
    SceneQualityReadiness,
    SceneQualityResponse,
    SceneValidationQualitySummary,
)

_VALIDATED_STATUSES = frozenset({SceneStatus.VALIDATED, SceneStatus.PROFILED})


def build_scene_quality(
    scene: SceneRecord,
    validation_run: SceneValidationRunRecord | None = None,
    profile_run: SceneProfileRunRecord | None = None,
) -> SceneQualityResponse:
    readiness = compute_scene_readiness(scene, validation_run)
    selectable, exclusion_reasons = _compute_selectability(
        scene, validation_run, readiness
    )

    return SceneQualityResponse(
        scene_id=scene.scene_id,
        dataset_id=scene.dataset_id,
        dataset_version=scene.dataset_version,
        status=str(getattr(scene.status, "value", scene.status)),
        counts=SceneQualityCounts(
            sample_count=scene.sample_count or 0,
            frame_count=scene.frame_count or 0,
            annotation_count=scene.annotation_count,
        ),
        ground_truth=SceneGroundTruthQualitySummary(
            has_ground_truth=scene.has_ground_truth,
            annotation_count=scene.annotation_count,
            ground_truth_source=scene.ground_truth_source,
        ),
        validation=_build_validation_summary(validation_run),
        profile=_build_profile_summary(profile_run),
        readiness=readiness,
        selectable_for_detection=selectable,
        exclusion_reasons=exclusion_reasons,
    )


def compute_scene_readiness(
    scene: SceneRecord,
    validation_run: SceneValidationRunRecord | None,
) -> SceneQualityReadiness:
    if scene.status not in _VALIDATED_STATUSES:
        return SceneQualityReadiness.UNKNOWN

    if validation_run is None:
        return SceneQualityReadiness.UNKNOWN

    val_status = (validation_run.validation_status or "").lower()

    if validation_run.should_block_pipeline or val_status in ("failed", "error"):
        return SceneQualityReadiness.BLOCKED

    if val_status == "warning":
        return SceneQualityReadiness.WARNING

    if val_status == "ready":
        return SceneQualityReadiness.READY

    return SceneQualityReadiness.UNKNOWN


def _compute_selectability(
    scene: SceneRecord,
    validation_run: SceneValidationRunRecord | None,
    readiness: SceneQualityReadiness,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if scene.status not in _VALIDATED_STATUSES:
        reasons.append("scene_not_ready")

    if validation_run is None:
        reasons.append("validation_missing")
    elif validation_run.should_block_pipeline or (
        validation_run.validation_status or ""
    ).lower() in ("failed", "error"):
        reasons.append("validation_blocked")

    # Use canonical SceneRecord GT fields — set by register_scene from SceneManifest.
    if not scene.has_ground_truth or scene.annotation_count <= 0:
        reasons.append("missing_ground_truth")

    return len(reasons) == 0, reasons


def _build_validation_summary(
    run: SceneValidationRunRecord | None,
) -> SceneValidationQualitySummary | None:
    if run is None:
        return None
    return SceneValidationQualitySummary(
        run_id=run.run_id,
        status=str(getattr(run.status, "value", run.status)),
        validation_status=run.validation_status,
        should_block_pipeline=run.should_block_pipeline,
        checked_sample_count=run.checked_sample_count,
        checked_frame_count=run.checked_frame_count,
        blocking_issue_count=run.error_count,
        warning_count=run.warning_count,
        issue_count=run.issue_count,
        report_uri=run.validation_report_uri,
    )


def _build_profile_summary(
    run: SceneProfileRunRecord | None,
) -> SceneProfileQualitySummary | None:
    if run is None:
        return None
    return SceneProfileQualitySummary(
        run_id=run.run_id,
        status=str(getattr(run.status, "value", run.status)),
        sample_count=run.sample_count,
        frame_count=run.frame_count,
        annotation_count=run.annotation_count,
        observed_channels=list(run.observed_channels or []),
        profile_report_uri=run.profile_report_uri,
    )
