from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from sceneops_core.jobs.schemas import JobType

from .enums import PipelineType


class PipelineStepDefinition(SceneOpsBaseModel):
    name: str
    order: int
    job_type: JobType
    depends_on: list[str] = Field(default_factory=list)
    default_params: JsonDict = Field(default_factory=dict)


class PipelineDefinition(SceneOpsBaseModel):
    type: PipelineType
    name: str
    description: str | None = None
    steps: list[PipelineStepDefinition]
