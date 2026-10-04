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

    observation_count: int = 0
    state_count: int = 0
    action_count: int = 0
    event_count: int = 0

    issues: list[EpisodeValidationIssue] = Field(default_factory=list)
