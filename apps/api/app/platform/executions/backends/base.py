from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.executions.schemas import ExecutionDispatchResult


@runtime_checkable
class ExecutionDispatchBackend(Protocol):
    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult: ...
    async def dispatch_pipeline(
        self, pipeline_run_id: str
    ) -> ExecutionDispatchResult: ...
