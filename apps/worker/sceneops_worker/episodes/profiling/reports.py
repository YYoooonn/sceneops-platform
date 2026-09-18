from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class EpisodeProfileResult(SceneOpsBaseModel):
    episode_id: str

    frame_count: int = 0
    observation_count: int = 0
    action_count: int = 0

    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)

    control_frequency_hz: float | None = None

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None
    duration_us: int | None = None

    task: str | None = None
    outcome: str | None = None
    mission_id: str | None = None
