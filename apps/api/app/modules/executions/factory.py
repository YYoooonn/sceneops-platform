from __future__ import annotations

from functools import lru_cache

from celery import Celery


@lru_cache
def create_celery_app(
    *,
    broker_url: str,
    result_backend: str,
) -> Celery:
    return Celery(
        "sceneops_api",
        broker=broker_url,
        backend=result_backend,
    )
