"""Scene quality response builder.

Pure functions — receive already-fetched records, return SceneQualityResponse.
Independently testable without a running service or DB.

Only run records that assessed the Scene's current manifest revision count;
any other run passed in is ignored, so a replaced Scene reports ``unknown``
until its new revision is validated (ADR-007 §13.4).
"""

from __future__ import annotations

from typing import TypeVar

from sceneops_core.scenes import (
    SceneReadiness,
    derive_scene_readiness,
    latest_validation_for_revision,
)
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.scenes.schemas import (
    SceneGroundTruthQualitySummary,
    SceneProfileQualitySummary,
    SceneQualityCounts,
    SceneQualityResponse,
    SceneValidationQualitySummary,
)


def build_scene_quality(
    scene: SceneRecord,
    validation_run: SceneValidationRunRecord | None = None,
    profile_run: SceneProfileRunRecord | None = None,
) -> SceneQualityResponse:
    validation_run = _current(scene, validation_run)
    profile_run = _current(scene, profile_run)
    readiness = compute_scene_readiness(scene, validation_run)
    selectable, exclusion_reasons = _compute_selectability(scene, readiness)

    return SceneQualityResponse(
        scene_id=scene.scene_id,
        dataset_id=scene.dataset_id,
        dataset_version=scene.dataset_version,
        manifest_artifact_id=scene.manifest_artifact_id,
        manifest_checksum=scene.manifest_checksum,
        counts=SceneQualityCounts(
            keyframe_count=scene.keyframe_count,
            observation_count=scene.observation_count,
            annotation_count=scene.annotation_count,
        ),
        ground_truth=SceneGroundTruthQualitySummary(
            has_ground_truth=scene.has_ground_truth,
            annotation_count=scene.annotation_count,
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
) -> SceneReadiness:
    run = latest_validation_for_revision(
        [validation_run] if validation_run is not None else [],
        scene_id=scene.scene_id,
        manifest_artifact_id=scene.manifest_artifact_id,
        manifest_checksum=scene.manifest_checksum,
    )
    return derive_scene_readiness(run)


_RunT = TypeVar("_RunT", SceneValidationRunRecord, SceneProfileRunRecord)


def _current(scene: SceneRecord, run: _RunT | None) -> _RunT | None:
    if run is None or not run.assessed(
        manifest_artifact_id=scene.manifest_artifact_id,
        manifest_checksum=scene.manifest_checksum,
    ):
        return None
    return run


def _compute_selectability(
    scene: SceneRecord,
    readiness: SceneReadiness,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if readiness == SceneReadiness.UNKNOWN:
        reasons.append("validation_missing")
    elif readiness == SceneReadiness.BLOCKED:
        reasons.append("validation_blocked")

    if not scene.has_ground_truth:
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
        manifest_artifact_id=run.manifest_artifact_id,
        validation_status=run.validation_status,
        should_block_pipeline=run.should_block_pipeline,
        checked_observation_count=run.checked_observation_count,
        checked_keyframe_count=run.checked_keyframe_count,
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
        manifest_artifact_id=run.manifest_artifact_id,
        observation_count=run.observation_count,
        keyframe_count=run.keyframe_count,
        annotation_count=run.annotation_count,
        observed_channels=list(run.observed_channels or []),
        profile_report_uri=run.profile_report_uri,
    )
