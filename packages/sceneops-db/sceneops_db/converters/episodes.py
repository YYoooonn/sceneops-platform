from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeRevisionRecord,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunType

from sceneops_db.models.episodes import (
    EpisodeModel,
    EpisodeRevisionModel,
    EpisodeRunRecordModel,
)

from ._utils import base_run_to_values, error_from_json, metadata_from_model

EpisodeRunRecord: TypeAlias = EpisodeValidationRunRecord | EpisodeProfileRunRecord

_TOPIC_LISTS = (
    "observation_topics",
    "state_topics",
    "action_topics",
    "event_topics",
    "transition_topics",
)
_COUNTS = (
    "observation_count",
    "state_count",
    "action_count",
    "event_count",
    "transition_count",
)

# ── Episode unit / revision ──────────────────────────────────────────────────


def episode_model_to_record(model: EpisodeModel) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=model.episode_id,
        source_kind=model.source_kind,
        robot_run_id=model.robot_run_id,
        content_id=model.content_id,
        unit_key=model.unit_key,
        registered_at=model.registered_at,
    )


def episode_unit_to_values(record: EpisodeRevisionRecord) -> dict[str, Any]:
    """The unit row of a revision's unit. ``registered_at`` is
    database-managed and never written from a record."""
    return {
        "episode_id": record.episode_id,
        "source_kind": record.source_kind,
        "robot_run_id": record.robot_run_id,
        "content_id": record.content_id,
        "unit_key": record.unit_key,
    }


def episode_revision_model_to_record(
    revision: EpisodeRevisionModel, unit: EpisodeModel
) -> EpisodeRevisionRecord:
    return EpisodeRevisionRecord(
        episode_id=revision.episode_id,
        source_kind=unit.source_kind,
        robot_run_id=unit.robot_run_id,
        content_id=unit.content_id,
        unit_key=unit.unit_key,
        manifest_artifact_id=revision.manifest_artifact_id,
        manifest_checksum=revision.manifest_checksum,
        producer_fingerprint=revision.producer_fingerprint,
        window_clock=revision.window_clock,
        window_start_timestamp_ns=revision.window_start_timestamp_ns,
        window_end_timestamp_ns=revision.window_end_timestamp_ns,
        **{name: list(getattr(revision, name) or []) for name in _TOPIC_LISTS},
        **{name: getattr(revision, name) for name in _COUNTS},
        registered_at=revision.registered_at,
    )


def episode_revision_to_values(record: EpisodeRevisionRecord) -> dict[str, Any]:
    """Every revision column the registrar writes; the unit binding lives on
    the unit row only."""
    return {
        "episode_id": record.episode_id,
        "manifest_checksum": record.manifest_checksum,
        "manifest_artifact_id": record.manifest_artifact_id,
        "producer_fingerprint": record.producer_fingerprint,
        "window_clock": record.window_clock,
        "window_start_timestamp_ns": record.window_start_timestamp_ns,
        "window_end_timestamp_ns": record.window_end_timestamp_ns,
        **{name: list(getattr(record, name)) for name in _TOPIC_LISTS},
        **{name: getattr(record, name) for name in _COUNTS},
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
        episode_id=model.episode_id,
        manifest_artifact_id=model.manifest_artifact_id,
        manifest_checksum=model.manifest_checksum,
    )

    s = model.summary or {}
    if model.type == RunType.EPISODE_VALIDATION.value:
        return EpisodeValidationRunRecord(
            **base,
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
            profile_report_uri=model.report_uri,
            checked_episode_count=s.get("checked_episode_count"),
            **{name: s.get(name) for name in _COUNTS},
            **{name: s.get(name, []) for name in _TOPIC_LISTS},
            window_duration_ns=s.get("window_duration_ns"),
            summary=s,
        )
    raise ValueError(f"Unknown episode run type: {model.type!r}")


def episode_run_record_to_values(record: EpisodeRunRecord) -> dict[str, Any]:
    base = {
        **base_run_to_values(record),
        "episode_id": record.episode_id,
        "manifest_artifact_id": record.manifest_artifact_id,
        "manifest_checksum": record.manifest_checksum,
    }

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
        return {**base, "report_uri": record.validation_report_uri, "summary": summary}

    summary = {
        **(record.summary or {}),
        "checked_episode_count": record.checked_episode_count,
        **{name: getattr(record, name) for name in _COUNTS},
        **{name: list(getattr(record, name)) for name in _TOPIC_LISTS},
        "window_duration_ns": record.window_duration_ns,
    }
    return {**base, "report_uri": record.profile_report_uri, "summary": summary}
