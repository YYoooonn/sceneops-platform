from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.executions.schemas import ExecutionDispatchResult


@runtime_checkable
class JobExecutionBackend(Protocol):
    """Hands a committed Job to the backend that runs it through JobRunner."""

    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult: ...


@runtime_checkable
class PipelineExecutionBackend(Protocol):
    """Hands a QUEUED PipelineRun to its orchestrator, which starts it and submits
    its tasks' Jobs to the job backend; nothing a pipeline does runs outside a Job."""

    async def dispatch_pipeline(
        self, pipeline_run_id: str
    ) -> ExecutionDispatchResult: ...
