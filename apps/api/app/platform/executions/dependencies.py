from __future__ import annotations

from typing import Annotated

from celery import Celery
from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import ExecutionRecordRepositoryDep
from app.platform.executions.dispatchers import (
    AirflowExecutionDispatcher,
    CeleryExecutionDispatcher,
    ExecutionDispatcher,
)
from app.platform.executions.factory import create_celery_app
from app.platform.executions.service import ExecutionService
from sceneops_core.executions.schemas import ExecutionBackend


def get_celery_app(settings: ApiSettingsDep) -> Celery:
    c = settings.execution.celery
    return create_celery_app(broker_url=c.broker_url, result_backend=c.result_backend)


CeleryAppDep = Annotated[Celery, Depends(get_celery_app)]


def get_execution_dispatcher(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> ExecutionDispatcher:
    backend = settings.execution.backend
    if backend == ExecutionBackend.CELERY:
        c = settings.execution.celery
        return CeleryExecutionDispatcher(
            app=celery_app,
            pipeline_queue=c.pipeline_queue,
            job_queue=c.job_queue,
        )
    if backend == ExecutionBackend.AIRFLOW:
        a = settings.execution.airflow
        return AirflowExecutionDispatcher(
            base_url=a.base_url,
            username=a.username,
            password=a.password,
            pipeline_dag_id=a.pipeline_dag_id,
            job_dag_id=a.job_dag_id,
        )
    raise ValueError(f"Unsupported execution backend: {backend}")


ExecutionDispatcherDep = Annotated[
    ExecutionDispatcher, Depends(get_execution_dispatcher)
]


def get_execution_service(
    dispatcher: ExecutionDispatcherDep,
    record_repository: ExecutionRecordRepositoryDep,
) -> ExecutionService:
    return ExecutionService(
        dispatcher=dispatcher,
        record_repository=record_repository,
    )


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
