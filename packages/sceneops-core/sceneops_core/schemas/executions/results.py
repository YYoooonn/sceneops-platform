from __future__ import annotations

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.executions.enums import ExecutionBackend, ExecutionKind, ExecutionStatus


class ExecutionDispatchResult(SceneOpsBaseModel):
    execution_id: str
    execution_backend: ExecutionBackend
    execution_kind: ExecutionKind
    resource_id : str
    status: ExecutionStatus = ExecutionStatus.QUEUED

    # Celery task id, Airflow dag_run_id, K8s job name 등 외부 실행 ID.
    # 지금은 execution_id와 동일하게
    external_id: str | None = Field(default=None)
