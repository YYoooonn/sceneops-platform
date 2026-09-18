from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunType

from sceneops_db.models.episodes import EpisodeModel, EpisodeRunRecordModel

from ._utils import (
    base_run_to_values,
    enum_to_value,
    error_from_json,
    metadata_from_model,
)

EpisodeRunRecord: TypeAlias = EpisodeValidationRunRecord | EpisodeProfileRunRecord

# ── Episode ──────────────────────────────────────────────────────────────────


def episode_model_to_record(model: EpisodeModel) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=model.episode_id,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        raw_log_id=model.raw_log_id,
        robot_id=model.robot_id,
        robot_run_id=model.robot_run_id,
        mission_id=model.mission_id,
        status=model.status,
        task=model.task,
        outcome=model.outcome,
        episode_manifest_uri=model.episode_manifest_uri,
        observation_channels=list(model.observation_channels or []),
        action_channels=list(model.action_channels or []),
        control_frequency_hz=model.control_frequency_hz,
        frame_count=model.frame_count,
        started_at=model.started_at,
        ended_at=model.ended_at,
        metadata=metadata_from_model(model),
    )


def episode_record_to_values(record: EpisodeRecord) -> dict[str, Any]:
    return {
        "episode_id": record.episode_id,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "raw_log_id": record.raw_log_id,
        "robot_id": record.robot_id,
        "robot_run_id": record.robot_run_id,
        "mission_id": record.mission_id,
        "status": enum_to_value(record.status),
        "task": record.task,
        "outcome": enum_to_value(record.outcome),
        "episode_manifest_uri": record.episode_manifest_uri,
        "observation_channels": list(record.observation_channels or []),
        "action_channels": list(record.action_channels or []),
        "control_frequency_hz": record.control_frequency_hz,
        "frame_count": record.frame_count,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "metadata_": record.metadata or {},
    }


# ── EpisodeRunRecord ─────────────────────────────────────────────────────────


def episode_run_model_to_record(model: EpisodeRunRecordModel) -> EpisodeRunRecord:
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
    )

    s = model.summary or {}
    if model.type == RunType.EPISODE_VALIDATION.value:
        return EpisodeValidationRunRecord(
            **base,
            episode_id=model.episode_id,
            episode_manifest_uri=model.episode_manifest_uri,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            validation_report_uri=model.report_uri,
            validation_status=s.get("validation_status"),
            should_block_pipeline=s.get("should_block_pipeline", False),
            checked_episode_count=s.get("checked_episode_count"),
            issue_count=s.get("issue_count"),
            error_count=s.get("error_count"),
            warning_count=s.get("warning_count"),
            summary=s,
        )
    if model.type == RunType.EPISODE_PROFILE.value:
        return EpisodeProfileRunRecord(
            **base,
            episode_id=model.episode_id,
            episode_manifest_uri=model.episode_manifest_uri,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            profile_report_uri=model.report_uri,
            checked_episode_count=s.get("checked_episode_count"),
            frame_count=s.get("frame_count"),
            observation_count=s.get("observation_count"),
            action_count=s.get("action_count"),
            observation_channels=s.get("observation_channels", []),
            action_channels=s.get("action_channels", []),
            control_frequency_hz=s.get("control_frequency_hz"),
            duration_us=s.get("duration_us"),
            task=s.get("task"),
            outcome=s.get("outcome"),
            mission_id=s.get("mission_id"),
            summary=s,
        )
    raise ValueError(f"Unknown episode run type: {model.type!r}")


def episode_run_record_to_values(record: EpisodeRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)

    if isinstance(record, EpisodeValidationRunRecord):
        summary = {
            **(record.summary or {}),
            "validation_status": record.validation_status,
            "should_block_pipeline": record.should_block_pipeline,
            "checked_episode_count": record.checked_episode_count,
            "issue_count": record.issue_count,
            "error_count": record.error_count,
            "warning_count": record.warning_count,
        }
        return {
            **base,
            "episode_id": record.episode_id,
            "episode_manifest_uri": record.episode_manifest_uri,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "report_uri": record.validation_report_uri,
            "summary": summary,
        }

    # EpisodeProfileRunRecord
    summary = {
        **(record.summary or {}),
        "checked_episode_count": record.checked_episode_count,
        "frame_count": record.frame_count,
        "observation_count": record.observation_count,
        "action_count": record.action_count,
        "observation_channels": record.observation_channels,
        "action_channels": record.action_channels,
        "control_frequency_hz": record.control_frequency_hz,
        "duration_us": record.duration_us,
        "task": record.task,
        "outcome": record.outcome,
        "mission_id": record.mission_id,
    }
    return {
        **base,
        "episode_id": record.episode_id,
        "episode_manifest_uri": record.episode_manifest_uri,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "report_uri": record.profile_report_uri,
        "summary": summary,
    }
