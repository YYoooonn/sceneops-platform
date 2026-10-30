from __future__ import annotations

import asyncio
from dataclasses import dataclass

from celery import Celery

from sceneops_core.constants.tasks import JOB_RUN_TASK, PIPELINE_ADVANCE_TASK
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)

# `Celery.send_task` is synchronous and waits for the broker (including its
# connection retries). These backends are awaited on the API's shared event loop,
# so the send runs on a worker thread: a slow or unreachable broker delays only the
# dispatching request, not every request the API is serving.


@dataclass(frozen=True)
class CeleryJobExecutionBackend:
    app: Celery
    job_queue: str

    async def dispatch_job(self, job_id: str) -> ExecutionDispatchResult:
        result = await asyncio.to_thread(
            self.app.send_task,
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
        )


@dataclass(frozen=True)
class CeleryPipelineExecutionBackend:
    app: Celery
    pipeline_queue: str

    async def dispatch_pipeline(self, pipeline_run_id: str) -> ExecutionDispatchResult:
        # The first orchestration step of the run; every later step is sent by
        # the worker that finished the run's current Job.
        result = await asyncio.to_thread(
            self.app.send_task,
            PIPELINE_ADVANCE_TASK,
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
        )
