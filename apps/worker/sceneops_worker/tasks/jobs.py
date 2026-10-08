from __future__ import annotations

from typing import Any

from celery.utils.log import get_task_logger

from sceneops_core.constants.tasks import JOB_RUN_TASK
from sceneops_db.session import (
    async_session_scope,
    dispose_async_engine,
    reset_async_engine_cache,
)
from sceneops_worker.celery_app import celery_app, worker_node
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.celery_app import create_execution_dispatcher
from sceneops_worker.jobs.runner import JobNotClaimableError, JobRunner
from sceneops_worker.runtime.async_runner import AsyncRuntimeRunner

logger = get_task_logger(__name__)


# No Celery-level retry: a Job's outcome, failure included, is persisted by
# JobRunner, and running a Job again is an explicit redispatch (POST
# /jobs/{id}/execute, a pipeline re-execution), never a replay of this message.
@celery_app.task(name=JOB_RUN_TASK, bind=True)
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
                dispatcher = create_execution_dispatcher(context.settings.execution)
                job = await JobRunner(
                    context,
                    dispatcher=dispatcher,
                    worker_node=worker_node(),
                ).run(job_id)

            return {"job_id": job_id, "status": job.status.value}
        except JobNotClaimableError as refused:
            # A duplicate or late message (a resend, a redelivery): delivery is at
            # least once and the claim decides, so this is not a task failure.
            # One line per refusal; their rate is the cost of duplicate messages.
            logger.warning(
                "job claim refused: %s",
                refused,
                extra={"job_id": job_id, "celery_task_id": celery_task_id},
            )
            return {"job_id": job_id, "status": "not_claimed"}
        finally:
            await dispose_async_engine()

    return AsyncRuntimeRunner.run(_run())
