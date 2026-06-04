from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import ExecutionBackend, ExecutionKind, ExecutionStatus


class ExecutionDispatchResult(SceneOpsBaseModel):
    execution_id: str
    execution_backend: ExecutionBackend
    execution_kind: ExecutionKind
    resource_id: str
    status: ExecutionStatus = ExecutionStatus.QUEUED

    external_id: str | None = Field(default=None)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
