from __future__ import annotations

from typing import Annotated

from celery import Celery
from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import ExecutionRecordRepositoryDep
from app.platform.executions.backends import (
    CeleryExecutionDispatchBackend,
    ExecutionDispatchBackend,
)
from app.platform.executions.factory import create_celery_app
from app.platform.executions.service import ExecutionService
from sceneops_core.executions.schemas import ExecutionBackend


def get_celery_app(settings: ApiSettingsDep) -> Celery:
    c = settings.execution.celery
    return create_celery_app(broker_url=c.broker_url, result_backend=c.result_backend)


CeleryAppDep = Annotated[Celery, Depends(get_celery_app)]


def get_execution_backend(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> ExecutionDispatchBackend:
    backend = settings.execution.backend
    if backend == ExecutionBackend.CELERY:
        c = settings.execution.celery
        return CeleryExecutionDispatchBackend(
            app=celery_app,
            job_queue=c.job_queue,
            pipeline_queue=c.pipeline_queue,
        )
    raise ValueError(f"Unsupported execution backend: {backend}")


ExecutionBackendDep = Annotated[
    ExecutionDispatchBackend, Depends(get_execution_backend)
]


def get_execution_service(
    backend: ExecutionBackendDep,
    record_repository: ExecutionRecordRepositoryDep,
) -> ExecutionService:
    return ExecutionService(
        backend=backend,
        record_repository=record_repository,
    )


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
