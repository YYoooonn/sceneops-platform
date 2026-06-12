from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_core.pipelines.schemas import PipelineDefinition
from sceneops_core.pipelines.schemas.manifests import (
    PipelineRunManifest,
    PipelineTaskRunManifest,
)


class PipelineRunDetailResponse(SceneOpsBaseModel):
    pipeline_run: PipelineRunManifest
    tasks: list[PipelineTaskRunManifest] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class PipelineRunListResponse(SceneOpsBaseModel):
    pipeline_runs: list[PipelineRunManifest]
    count: int
    metadata: JsonDict = Field(default_factory=dict)


class PipelineTaskRunListResponse(SceneOpsBaseModel):
    tasks: list[PipelineTaskRunManifest]
    count: int
    metadata: JsonDict = Field(default_factory=dict)


class PipelineDefinitionResponse(SceneOpsBaseModel):
    definition: PipelineDefinition


class PipelineDefinitionListResponse(SceneOpsBaseModel):
    definitions: list[PipelineDefinition]
    count: int


class PipelineExecuteResponse(SceneOpsBaseModel):
    execution: ExecutionDispatchResult
