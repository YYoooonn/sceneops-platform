from __future__ import annotations

from typing import Any

from celery.utils.log import get_task_logger

from sceneops_core.constants.tasks import JOB_RUN_TASK
from sceneops_db.session import (
    async_session_scope,
    dispose_async_engine,
    reset_async_engine_cache,
)
from sceneops_worker.celery_app import celery_app
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.runtime.async_runner import AsyncRuntimeRunner

logger = get_task_logger(__name__)


@celery_app.task(
    name=JOB_RUN_TASK,
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_job_task(
    self,
    job_id: str,
) -> dict[str, Any]:
    celery_task_id = self.request.id
    worker_id = f"celery:{celery_task_id}"

    logger.info(
        "Starting job task",
        extra={"job_id": job_id, "celery_task_id": celery_task_id},
    )

    reset_async_engine_cache()

    async def _run() -> dict[str, Any]:
        try:
            async with async_session_scope() as session:
                context = create_worker_context(session, worker_id=worker_id)
                result = await JobRunner(context).run(job_id)

            return {"job_id": job_id, "status": result.status.value}
        finally:
            await dispose_async_engine()

    return AsyncRuntimeRunner.run(_run())
