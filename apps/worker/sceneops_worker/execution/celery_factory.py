from __future__ import annotations

from celery import Celery

from sceneops_core.config import CelerySettings
from sceneops_core.constants.tasks import JOB_RUN_TASK, PIPELINE_RUN_TASK


def create_celery_app(
    *,
    name: str,
    settings: CelerySettings,
    include: list[str] | None = None,
) -> Celery:
    app = Celery(
        name,
        broker=settings.broker_url,
        backend=settings.result_backend,
        include=include or [],
    )

    configure_celery_app(
        app=app,
        settings=settings,
    )

    return app


def configure_celery_app(
    *,
    app: Celery,
    settings: CelerySettings,
) -> None:
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=settings.task_acks_late,
        task_reject_on_worker_lost=settings.task_reject_on_worker_lost,
        worker_prefetch_multiplier=settings.worker_prefetch_multiplier,
        task_default_queue=settings.task_default_queue,
        task_default_exchange=settings.task_default_queue,
        task_default_exchange_type="direct",
        task_default_routing_key=settings.task_default_queue,
        task_routes={
            PIPELINE_RUN_TASK: {
                "queue": settings.pipeline_queue,
                "routing_key": settings.pipeline_queue,
            },
            JOB_RUN_TASK: {
                "queue": settings.job_queue,
                "routing_key": settings.job_queue,
            },
        },
        worker_redirect_stdouts=True,
        worker_redirect_stdouts_level="INFO",
    )
