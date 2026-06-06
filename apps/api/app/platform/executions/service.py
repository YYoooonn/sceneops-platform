from __future__ import annotations

from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)
from sceneops_db.repositories.executions import ExecutionRecordRepository

from app.platform.executions.backends.base import ExecutionDispatchBackend


class ExecutionService:
    def __init__(
        self,
        *,
        backend: ExecutionDispatchBackend,
        record_repository: ExecutionRecordRepository,
    ) -> None:
        self._backend = backend
        self._record_repository = record_repository

    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        result = await self._backend.dispatch_job(job_id)
        return await self._record_repository.create(result)

    async def dispatch_pipeline(self, pipeline_run_id: str) -> ExecutionDispatchResult:
        result = await self._backend.dispatch_pipeline(pipeline_run_id)
        return await self._record_repository.create(result)

    async def get_execution(self, execution_id: str) -> ExecutionDispatchResult | None:
        return await self._record_repository.get(execution_id)

    async def list_executions(
        self,
        *,
        execution_backend: ExecutionBackend | None = None,
        execution_kind: ExecutionKind | None = None,
        resource_id: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExecutionDispatchResult]:
        return await self._record_repository.list(
            execution_backend=execution_backend,
            execution_kind=execution_kind,
            resource_id=resource_id,
            status=status,
            limit=limit,
            offset=offset,
        )
