from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from .enums import PipelineType


class PipelineStepDefinition(SceneOpsBaseModel):
    name: str
    order: int
    job_type: str
    depends_on: list[str] = Field(default_factory=list)
    default_params: JsonDict = Field(default_factory=dict)


class PipelineDefinition(SceneOpsBaseModel):
    type: PipelineType
    name: str
    description: str | None = None
    steps: list[PipelineStepDefinition]
