from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.runs.schemas import RunType
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from sceneops_db.models.scenes import SceneModel, SceneRunRecordModel

from ._utils import (
    base_run_to_values,
    enum_to_value,
    error_from_json,
    metadata_from_model,
)

SceneRunRecord: TypeAlias = SceneValidationRunRecord | SceneProfileRunRecord

_SCENE_RUN_TYPE_MAP: dict[str, type[SceneRunRecord]] = {
    RunType.SCENE_VALIDATION.value: SceneValidationRunRecord,
    RunType.SCENE_PROFILE.value: SceneProfileRunRecord,
}


# ── Scene ─────────────────────────────────────────────────────────────────────


def scene_model_to_record(model: SceneModel) -> SceneRecord:
    return SceneRecord(
        scene_id=model.scene_id,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        source_kind=model.source_kind,
        external_format=model.external_format,
        robot_run_id=model.robot_run_id,
        source_unit_key=model.source_unit_key,
        producer_fingerprint=model.producer_fingerprint,
        manifest_artifact_id=model.manifest_artifact_id,
        manifest_checksum=model.manifest_checksum,
        window_clock=model.window_clock,
        window_start_timestamp_ns=model.window_start_timestamp_ns,
        window_end_timestamp_ns=model.window_end_timestamp_ns,
        observed_channels=list(model.observed_channels or []),
        observation_count=model.observation_count,
        keyframe_count=model.keyframe_count,
        annotation_count=model.annotation_count,
        registered_at=model.registered_at,
        updated_at=model.updated_at,
    )


def scene_record_to_values(record: SceneRecord) -> dict[str, Any]:
    """Every registrar-owned column. ``registered_at`` / ``updated_at`` are
    database-managed and never written from a record."""
    return {
        "scene_id": record.scene_id,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "source_kind": enum_to_value(record.source_kind),
        "external_format": record.external_format,
        "robot_run_id": record.robot_run_id,
        "source_unit_key": record.source_unit_key,
        "producer_fingerprint": record.producer_fingerprint,
        "manifest_artifact_id": record.manifest_artifact_id,
        "manifest_checksum": record.manifest_checksum,
        "window_clock": record.window_clock,
        "window_start_timestamp_ns": record.window_start_timestamp_ns,
        "window_end_timestamp_ns": record.window_end_timestamp_ns,
        "observed_channels": list(record.observed_channels),
        "observation_count": record.observation_count,
        "keyframe_count": record.keyframe_count,
        "annotation_count": record.annotation_count,
    }


# ── SceneRunRecord ────────────────────────────────────────────────────────────


def scene_run_model_to_record(model: SceneRunRecordModel) -> SceneRunRecord:
    cls = _SCENE_RUN_TYPE_MAP.get(model.type)
    if cls is None:
        raise ValueError(f"Unknown scene run type: {model.type!r}")

    base = dict(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_task_run_id=model.pipeline_task_run_id,
        job_id=model.job_id,
        params=model.params or {},
        result=model.result,
        error=error_from_json(model.error),
        artifact_root_uri=model.artifact_root_uri,
        manifest_uri=model.manifest_uri,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
        scene_id=model.scene_id,
        manifest_artifact_id=model.manifest_artifact_id,
        manifest_checksum=model.manifest_checksum,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
    )
    s = model.summary or {}

    if model.type == RunType.SCENE_VALIDATION.value:
        return SceneValidationRunRecord(
            **base,
            validation_report_uri=model.report_uri,
            validation_status=s.get("validation_status"),
            should_block_pipeline=s.get("should_block_pipeline", False),
            checked_observation_count=s.get("checked_observation_count"),
            checked_keyframe_count=s.get("checked_keyframe_count"),
            issue_count=s.get("issue_count"),
            error_count=s.get("error_count"),
            warning_count=s.get("warning_count"),
            missing_channel_count=s.get("missing_channel_count"),
            summary=s,
        )
    return SceneProfileRunRecord(
        **base,
        profile_report_uri=model.report_uri,
        observation_count=s.get("observation_count"),
        keyframe_count=s.get("keyframe_count"),
        annotation_count=s.get("annotation_count"),
        observed_channels=s.get("observed_channels", []),
        coverage=s.get("coverage", {}),
        annotation_summary=s.get("annotation_summary", {}),
    )


def scene_run_record_to_values(record: SceneRunRecord) -> dict[str, Any]:
    base = {
        **base_run_to_values(record),
        "scene_id": record.scene_id,
        "manifest_artifact_id": record.manifest_artifact_id,
        "manifest_checksum": record.manifest_checksum,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
    }

    if isinstance(record, SceneValidationRunRecord):
        summary = {
            **(record.summary or {}),
            "validation_status": record.validation_status,
            "should_block_pipeline": record.should_block_pipeline,
            "checked_observation_count": record.checked_observation_count,
            "checked_keyframe_count": record.checked_keyframe_count,
            "issue_count": record.issue_count,
            "error_count": record.error_count,
            "warning_count": record.warning_count,
            "missing_channel_count": record.missing_channel_count,
        }
        return {**base, "report_uri": record.validation_report_uri, "summary": summary}

    summary = {
        "observation_count": record.observation_count,
        "keyframe_count": record.keyframe_count,
        "annotation_count": record.annotation_count,
        "observed_channels": record.observed_channels,
        "coverage": record.coverage,
        "annotation_summary": record.annotation_summary,
    }
    return {**base, "report_uri": record.profile_report_uri, "summary": summary}
