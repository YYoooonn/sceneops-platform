from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.executions import (
    ExecutionBackend,
    ExecutionKind,
    ExecutionStatus,
)


class PipelineExecutionResponse(SceneOpsBaseModel):
    pipeline_run_id: str
    execution_id: str
    execution_backend: ExecutionBackend
    execution_kind: ExecutionKind
    status: ExecutionStatus
    queued: bool = True
