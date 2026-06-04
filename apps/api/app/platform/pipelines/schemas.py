from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_core.pipelines.schemas import PipelineDefinition


class PipelineDefinitionResponse(SceneOpsBaseModel):
    definition: PipelineDefinition


class PipelineDefinitionListResponse(SceneOpsBaseModel):
    definitions: list[PipelineDefinition]
    count: int


class PipelineExecuteResponse(SceneOpsBaseModel):
    execution: ExecutionDispatchResult
