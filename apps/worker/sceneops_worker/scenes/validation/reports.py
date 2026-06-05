from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class SceneValidationIssue(SceneOpsBaseModel):
    type: str
    message: str
    blocking: bool = False
    channel: str | None = None


class SceneValidationResult(SceneOpsBaseModel):
    scene_id: str

    status: str
    should_block: bool

    required_channels: list[str] = Field(default_factory=list)
    observed_channels: list[str] = Field(default_factory=list)
    missing_channels: list[str] = Field(default_factory=list)

    sample_count: int = 0
    frame_count: int = 0

    issues: list[SceneValidationIssue] = Field(default_factory=list)
