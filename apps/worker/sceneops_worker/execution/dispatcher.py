"""The worker's port to the execution backends.

Two messages leave a worker process, and only these two:

    dispatch_job(job_id)                 the PipelineOrchestrator submits a Job it
                                         created; a job worker runs it through JobRunner
    advance_pipeline(pipeline_run_id)    JobRunner reports that a pipeline-owned Job
                                         reached a terminal state; the orchestrator
                                         takes the run's next step

Both carry identifiers only. PostgreSQL holds every state they refer to, so a
duplicated or late message is harmless: JobRunner claims a Job at most once and an
orchestration step is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from celery import Celery

from sceneops_core.config import ExecutionSettings
from sceneops_core.constants.tasks import JOB_RUN_TASK, PIPELINE_ADVANCE_TASK
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)


class ExecutionDispatcher(Protocol):
    def dispatch_job(self, job_id: str) -> ExecutionDispatchResult: ...

    def advance_pipeline(self, pipeline_run_id: str) -> None: ...


@dataclass(frozen=True)
class CeleryExecutionDispatcher:
    app: Celery
    job_queue: str
    pipeline_queue: str

    def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
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

    def advance_pipeline(self, pipeline_run_id: str) -> None:
        self.app.send_task(
            PIPELINE_ADVANCE_TASK,
            args=[pipeline_run_id],
            queue=self.pipeline_queue,
            routing_key=self.pipeline_queue,
        )


def create_execution_dispatcher(settings: ExecutionSettings) -> ExecutionDispatcher:
    for name, backend in (
        ("job_backend", settings.job_backend),
        ("pipeline_backend", settings.pipeline_backend),
    ):
        if backend != ExecutionBackend.CELERY:
            raise ValueError(f"Unsupported execution {name}: {backend}")

    # The worker's own Celery app: it carries the task routes of both queues.
    from sceneops_worker.celery_app import celery_app

    return CeleryExecutionDispatcher(
        app=celery_app,
        job_queue=settings.celery.job_queue,
        pipeline_queue=settings.celery.pipeline_queue,
    )
