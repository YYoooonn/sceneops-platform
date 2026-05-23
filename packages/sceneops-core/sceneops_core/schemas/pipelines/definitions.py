from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.pipelines.enums import PipelineType


class PipelineStepDefinition(BaseModel):
    name: str
    order: int
    jobType: str
    dependsOn: list[str] = Field(default_factory=list)
    defaultParams: JsonDict = Field(default_factory=dict)


class PipelineDefinition(BaseModel):
    type: PipelineType
    name: str
    description: str | None = None
    steps: list[PipelineStepDefinition]
