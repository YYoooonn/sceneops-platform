from __future__ import annotations

from celery.signals import (
    celeryd_after_setup,
    worker_process_init,
    worker_process_shutdown,
)

from sceneops_db.session import reset_async_engine_cache
from sceneops_worker.config import get_settings
from sceneops_core.config import ExecutionSettings
from sceneops_execution.executions.celery_factory import create_celery_app
from sceneops_execution.executions.dispatcher import (
    CeleryExecutionDispatcher,
    ExecutionDispatcher,
)

settings = get_settings()

celery_app = create_celery_app(
    name="sceneops_worker",
    settings=settings.execution.celery,
    include=[
        "sceneops_worker.tasks.pipelines",
        "sceneops_worker.tasks.jobs",
    ],
)


def create_execution_dispatcher(settings: ExecutionSettings) -> ExecutionDispatcher:
    """The two messages a worker sends, over this worker's own Celery app: it
    carries the task routes of both queues."""
    return CeleryExecutionDispatcher(
        app=celery_app,
        job_queue=settings.celery.job_queue,
        pipeline_queue=settings.celery.pipeline_queue,
    )


# This worker's Celery node name ("<name>@<host>"), set in the main process
# before the pool forks, so every pool child inherits it. A task's
# ``request.hostname`` is only the host in a prefork child, which cannot tell two
# workers on one machine apart.
_worker_node: str | None = None


@celeryd_after_setup.connect
def on_worker_setup(sender: str, **_: object) -> None:
    global _worker_node
    _worker_node = sender


def worker_node() -> str | None:
    return _worker_node


@worker_process_init.connect
def on_worker_process_init(**_: object) -> None:
    """Reset DB singletons after Celery prefork.

    This prevents a child process from accidentally reusing parent-created
    SQLAlchemy async engine/sessionmaker references.
    """

    reset_async_engine_cache()


@worker_process_shutdown.connect
def on_worker_process_shutdown(**_: object) -> None:
    """Drop the child's DB singletons before it exits."""

    reset_async_engine_cache()
