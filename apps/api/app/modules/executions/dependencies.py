from __future__ import annotations

from typing import Annotated

from celery import Celery
from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.modules.executions import create_celery_app
from app.modules.executions.dispatchers import (
    AirflowExecutionDispatcher,
    CeleryExecutionDispatcher,
    ExecutionDispatcher,
)
from sceneops_core.executions.schemas import ExecutionBackend


def get_celery_app(
    settings: ApiSettingsDep,
) -> Celery:
    celery_settings = settings.execution.celery

    return create_celery_app(
        broker_url=celery_settings.broker_url,
        result_backend=celery_settings.result_backend,
    )


CeleryAppDep = Annotated[Celery, Depends(get_celery_app)]


def get_execution_dispatcher(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> ExecutionDispatcher:
    execution = settings.execution

    if execution.backend == ExecutionBackend.CELERY:
        return CeleryExecutionDispatcher(
            app=celery_app,
            pipeline_queue=execution.celery.pipeline_queue,
            job_queue=execution.celery.job_queue,
        )

    if execution.backend == ExecutionBackend.AIRFLOW:
        return AirflowExecutionDispatcher(
            base_url=execution.airflow.base_url,
            username=execution.airflow.username,
            password=execution.airflow.password,
            pipeline_dag_id=execution.airflow.pipeline_dag_id,
            job_dag_id=execution.airflow.job_dag_id,
        )

    raise ValueError(
        f"Unsupported execution backend for API dispatch: {execution.backend}"
    )


ExecutionDispatcherDep = Annotated[
    ExecutionDispatcher,
    Depends(get_execution_dispatcher),
]
