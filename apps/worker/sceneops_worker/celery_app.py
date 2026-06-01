from __future__ import annotations

from celery.signals import worker_process_init, worker_process_shutdown

from sceneops_db.session import reset_async_engine_cache
from sceneops_worker.config import get_settings
from sceneops_worker.execution import create_celery_app
from sceneops_worker.runtime.async_runner import shutdown_async_runtime_runner

settings = get_settings()

celery_app = create_celery_app(
    name="sceneops_worker",
    settings=settings.execution.celery,
    include=[
        "sceneops_worker.tasks.pipelines",
        "sceneops_worker.tasks.jobs",
    ],
)


@worker_process_init.connect
def on_worker_process_init(**_: object) -> None:
    """Reset DB singletons after Celery prefork.

    This prevents a child process from accidentally reusing parent-created
    SQLAlchemy async engine/sessionmaker references.
    """

    reset_async_engine_cache()


@worker_process_shutdown.connect
def on_worker_process_shutdown(**_: object) -> None:
    """Clean up async runtime resources before the worker child exits."""

    shutdown_async_runtime_runner()
    reset_async_engine_cache()
