from __future__ import annotations

from celery.signals import (
    celeryd_after_setup,
    worker_process_init,
    worker_process_shutdown,
)

from sceneops_db.session import reset_async_engine_cache
from sceneops_worker.config import get_settings
from sceneops_worker.execution import create_celery_app

settings = get_settings()

celery_app = create_celery_app(
    name="sceneops_worker",
    settings=settings.execution.celery,
    include=[
        "sceneops_worker.tasks.pipelines",
        "sceneops_worker.tasks.jobs",
    ],
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
