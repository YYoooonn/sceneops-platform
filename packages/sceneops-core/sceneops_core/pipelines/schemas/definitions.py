from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import JobType

from .enums import PipelineType


class PipelineTaskDefinition(SceneOpsBaseModel):
    pipeline_task_id: str
    name: str
    order: int
    job_type: JobType

    depends_on_pipeline_task_ids: list[str] = Field(default_factory=list)
    default_params: JsonDict = Field(default_factory=dict)

    optional: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class PipelineDefinition(SceneOpsBaseModel):
    type: PipelineType
    name: str
    description: str | None = None

    tasks: list[PipelineTaskDefinition]

    metadata: JsonDict = Field(default_factory=dict)
