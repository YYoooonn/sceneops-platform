from __future__ import annotations

import asyncio
from typing import Any

from celery.utils.log import get_task_logger

from sceneops_core.constants.tasks import PIPELINE_RUN_TASK
from sceneops_worker.celery_app import celery_app
from sceneops_worker.runtime.pipeline_runtime import PipelineRuntime

logger = get_task_logger(__name__)


@celery_app.task(
    name=PIPELINE_RUN_TASK,
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_pipeline_task(
    self,
    pipeline_run_id: str,
) -> dict[str, Any]:
    celery_task_id = self.request.id
    worker_id = f"celery:{celery_task_id}"

    logger.info(
        "Starting pipeline task",
        extra={
            "pipeline_run_id": pipeline_run_id,
            "celery_task_id": celery_task_id,
        },
    )

    return asyncio.run(
        _run_pipeline(
            pipeline_run_id=pipeline_run_id,
            worker_id=worker_id,
        )
    )


async def _run_pipeline(
    *,
    pipeline_run_id: str,
    worker_id: str,
) -> dict[str, Any]:
    runtime = PipelineRuntime(worker_id=worker_id)

    result = await runtime.run_pipeline(
        pipeline_run_id=pipeline_run_id,
    )

    return {
        "pipeline_run_id": pipeline_run_id,
        "status": result.status.value,
    }
