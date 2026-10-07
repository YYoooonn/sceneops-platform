from __future__ import annotations

from typing import Any

from celery.utils.log import get_task_logger

from sceneops_core.constants.tasks import PIPELINE_ADVANCE_TASK
from sceneops_db.session import (
    async_session_scope,
    dispose_async_engine,
    reset_async_engine_cache,
)
from sceneops_worker.celery_app import celery_app
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.execution.dispatcher import create_execution_dispatcher
from sceneops_worker.pipelines.orchestrator import PipelineOrchestrator
from sceneops_worker.runtime.async_runner import AsyncRuntimeRunner

logger = get_task_logger(__name__)


# One orchestration step: short, idempotent and never executing a Job. No
# Celery-level retry: the next Job report or an explicit re-execution advances
# the run again.
@celery_app.task(name=PIPELINE_ADVANCE_TASK, bind=True)
def advance_pipeline_task(
    self,
    pipeline_run_id: str,
) -> dict[str, Any]:
    celery_task_id = self.request.id
    worker_id = f"celery:{celery_task_id}"

    logger.info(
        "Advancing pipeline run",
        extra={"pipeline_run_id": pipeline_run_id, "celery_task_id": celery_task_id},
    )

    reset_async_engine_cache()

    async def _run() -> dict[str, Any]:
        try:
            async with async_session_scope() as session:
                context = create_worker_context(session, worker_id=worker_id)
                dispatcher = create_execution_dispatcher(context.settings.execution)
                run = await PipelineOrchestrator(
                    context, dispatcher=dispatcher
                ).advance(pipeline_run_id)

            return {"pipeline_run_id": pipeline_run_id, "status": run.status.value}
        finally:
            await dispose_async_engine()

    return AsyncRuntimeRunner.run(_run())
