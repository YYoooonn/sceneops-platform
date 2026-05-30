from __future__ import annotations

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
