from __future__ import annotations

from dataclasses import dataclass

from celery import Celery

from sceneops_core.constants.tasks import JOB_RUN_TASK, PIPELINE_RUN_TASK
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionKind,
    ExecutionStatus,
    ExecutionDispatchResult,
)
from app.modules.executions.dispatchers.base import ExecutionDispatcher


@dataclass(frozen=True)
class CeleryExecutionDispatcher(ExecutionDispatcher):
    app: Celery
    pipeline_queue: str
    job_queue: str

    def dispatch_pipeline_run(
        self,
        *,
        pipeline_run_id: str,
    ) -> ExecutionDispatchResult:
        async_result = self.app.send_task(
            PIPELINE_RUN_TASK,
            args=[pipeline_run_id],
            queue=self.pipeline_queue,
        )

        return ExecutionDispatchResult(
            execution_id=async_result.id,
            external_id=async_result.id,
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.PIPELINE_RUN,
            resource_id=pipeline_run_id,
            status=ExecutionStatus.QUEUED,
        )

    def dispatch_job_run(
        self,
        *,
        job_id: str,
    ) -> ExecutionDispatchResult:
        async_result = self.app.send_task(
            JOB_RUN_TASK,
            args=[job_id],
            queue=self.job_queue,
        )

        return ExecutionDispatchResult(
            execution_id=async_result.id,
            external_id=async_result.id,
            execution_backend=ExecutionBackend.CELERY,
            execution_kind=ExecutionKind.JOB_RUN,
            resource_id=job_id,
            status=ExecutionStatus.QUEUED,
        )
