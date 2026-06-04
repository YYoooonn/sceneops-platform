from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import JobType

from .enums import PipelineType


class PipelineStepDefinition(SceneOpsBaseModel):
    step_id: str
    name: str
    order: int
    job_type: JobType

    # step_id list
    depends_on: list[str] = Field(default_factory=list)
    default_params: JsonDict = Field(default_factory=dict)

    optional: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class PipelineDefinition(SceneOpsBaseModel):
    type: PipelineType
    name: str
    description: str | None = None

    steps: list[PipelineStepDefinition]

    metadata: JsonDict = Field(default_factory=dict)
