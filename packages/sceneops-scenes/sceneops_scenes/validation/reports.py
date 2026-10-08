from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class SceneValidationIssue(SceneOpsBaseModel):
    type: str
    message: str
    blocking: bool = False
    channel: str | None = None
    # How many observations / keyframes the issue covers, for issues that
    # are aggregated per channel instead of repeated per observation.
    count: int | None = None


class SceneValidationResult(SceneOpsBaseModel):
    scene_id: str

    status: str
    should_block: bool

    required_channels: list[str] = Field(default_factory=list)
    observed_channels: list[str] = Field(default_factory=list)
    missing_channels: list[str] = Field(default_factory=list)

    observation_count: int = 0
    keyframe_count: int = 0

    issues: list[SceneValidationIssue] = Field(default_factory=list)
