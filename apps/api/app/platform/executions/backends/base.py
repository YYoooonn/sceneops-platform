from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.executions.schemas import ExecutionDispatchResult


@runtime_checkable
class JobExecutionBackend(Protocol):
    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult: ...


@runtime_checkable
class PipelineExecutionBackend(Protocol):
    async def dispatch_pipeline(
        self, pipeline_run_id: str
    ) -> ExecutionDispatchResult: ...
