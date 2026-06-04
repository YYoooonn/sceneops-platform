from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.executions.schemas import ExecutionDispatchResult


class JobExecuteResponse(SceneOpsBaseModel):
    execution: ExecutionDispatchResult
