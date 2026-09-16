from __future__ import annotations

from typing import Any

from sceneops_core.episodes.schemas import EpisodeRecord

from sceneops_db.models.episodes import EpisodeModel

from ._utils import enum_to_value, metadata_from_model

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
