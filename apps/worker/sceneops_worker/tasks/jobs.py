from __future__ import annotations

from typing import Any

from celery.utils.log import get_task_logger

from sceneops_core.constants.tasks import JOB_RUN_TASK
from sceneops_worker.celery_app import celery_app
from sceneops_worker.runtime.async_runner import get_async_runtime_runner
from sceneops_worker.runtime.job_runtime import JobRuntime

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
        extra={
            "job_id": job_id,
            "celery_task_id": celery_task_id,
        },
    )

    runner = get_async_runtime_runner()

    return runner.run(
        _run_job(
            job_id=job_id,
            worker_id=worker_id,
        )
    )


async def _run_job(
    *,
    job_id: str,
    worker_id: str,
) -> dict[str, Any]:
    runtime = JobRuntime(worker_id=worker_id)

    result = await runtime.run_job(
        job_id=job_id,
    )

    return {
        "job_id": job_id,
        "status": result.status.value,
    }
