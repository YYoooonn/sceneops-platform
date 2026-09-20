from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class EpisodeValidationIssue(SceneOpsBaseModel):
    type: str
    message: str
    blocking: bool = False
    field: str | None = None


class EpisodeValidationResult(SceneOpsBaseModel):
    episode_id: str

    status: str
    should_block: bool

    checked_fields: list[str] = Field(default_factory=list)

    frame_count: int = 0
    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)

    issues: list[EpisodeValidationIssue] = Field(default_factory=list)
