from __future__ import annotations

from dataclasses import dataclass

from celery import Celery

from sceneops_core.constants.tasks import JOB_RUN_TASK, PIPELINE_RUN_TASK
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)


@dataclass(frozen=True)
class CeleryExecutionDispatchBackend:
    app: Celery
    job_queue: str
    pipeline_queue: str

    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        result = self.app.send_task(
            JOB_RUN_TASK,
            args=[job_id],
            queue=self.job_queue,
            routing_key=self.job_queue,
        )
        return ExecutionDispatchResult(
            execution_id=result.id,
            external_id=result.id,
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
            status=ExecutionStatus.QUEUED,
        )

    async def dispatch_pipeline(self, pipeline_run_id: str) -> ExecutionDispatchResult:
        result = self.app.send_task(
            PIPELINE_RUN_TASK,
            args=[pipeline_run_id],
            queue=self.pipeline_queue,
            routing_key=self.pipeline_queue,
        )
        return ExecutionDispatchResult(
            execution_id=result.id,
            external_id=result.id,
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.PIPELINE_RUN,
            resource_id=pipeline_run_id,
            status=ExecutionStatus.QUEUED,
        )
