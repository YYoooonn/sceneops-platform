from __future__ import annotations

from abc import ABC, abstractmethod

from sceneops_core.executions.schemas import ExecutionDispatchResult


class ExecutionDispatcher(ABC):
    @abstractmethod
    def dispatch_pipeline_run(
        self,
        *,
        pipeline_run_id: str,
    ) -> ExecutionDispatchResult:
        raise NotImplementedError

    @abstractmethod
    def dispatch_job_run(
        self,
        *,
        job_id: str,
    ) -> ExecutionDispatchResult:
        raise NotImplementedError
