from __future__ import annotations

from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)
from sceneops_db.repositories.executions import ExecutionRecordRepository

from app.platform.executions.dispatchers.base import ExecutionDispatcher


class ExecutionService:
    def __init__(
        self,
        *,
        dispatcher: ExecutionDispatcher,
        record_repository: ExecutionRecordRepository,
    ) -> None:
        self._dispatcher = dispatcher
        self._record_repository = record_repository

    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        result = self._dispatcher.dispatch_job_run(job_id=job_id)
        return await self._record_repository.create(result)

    async def dispatch_pipeline(self, pipeline_run_id: str) -> ExecutionDispatchResult:
        result = self._dispatcher.dispatch_pipeline_run(pipeline_run_id=pipeline_run_id)
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
