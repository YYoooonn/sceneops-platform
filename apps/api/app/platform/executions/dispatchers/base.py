from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.executions.schemas import ExecutionDispatchResult


@runtime_checkable
class ExecutionDispatcher(Protocol):
    def dispatch_job_run(self, *, job_id: str) -> ExecutionDispatchResult: ...
    def dispatch_pipeline_run(
        self, *, pipeline_run_id: str
    ) -> ExecutionDispatchResult: ...
