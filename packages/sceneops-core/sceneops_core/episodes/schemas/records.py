from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import EpisodeOutcome, EpisodeStatus


class EpisodeRecord(SceneOpsBaseModel):
    episode_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    # Lineage back into the raw-log / robot domains. Plain indexed columns,
    # not foreign keys — same cross-domain-reference convention as
    # RobotStateModel.scene_id (the referenced row may not exist yet, or may
    # live in a table this domain doesn't own).
    raw_log_id: str | None = None
    robot_id: str | None = None
    robot_run_id: str | None = None
    mission_id: str | None = None

    status: EpisodeStatus = EpisodeStatus.CREATED

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN

    episode_manifest_uri: str | None = None

    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)
    control_frequency_hz: float | None = None

    frame_count: int = 0

    started_at: datetime | None = None
    ended_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
