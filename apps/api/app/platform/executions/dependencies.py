from __future__ import annotations

from typing import Annotated

from celery import Celery
from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import ExecutionRecordRepositoryDep
from app.platform.executions.backends import (
    AirflowPipelineExecutionBackend,
    CeleryJobExecutionBackend,
    CeleryPipelineExecutionBackend,
    JobExecutionBackend,
    PipelineExecutionBackend,
)
from app.platform.executions.factory import create_celery_app
from app.platform.executions.service import ExecutionService
from sceneops_core.executions.schemas import ExecutionBackend


def get_celery_app(settings: ApiSettingsDep) -> Celery:
    c = settings.execution.celery
    return create_celery_app(broker_url=c.broker_url, result_backend=c.result_backend)


CeleryAppDep = Annotated[Celery, Depends(get_celery_app)]


def get_job_execution_backend(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> JobExecutionBackend:
    backend = settings.execution.job_backend
    if backend == ExecutionBackend.CELERY:
        c = settings.execution.celery
        return CeleryJobExecutionBackend(app=celery_app, job_queue=c.job_queue)
    raise ValueError(f"Unsupported job execution backend: {backend}")


JobExecutionBackendDep = Annotated[
    JobExecutionBackend, Depends(get_job_execution_backend)
]


def get_pipeline_execution_backend(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> PipelineExecutionBackend:
    backend = settings.execution.pipeline_backend
    if backend == ExecutionBackend.CELERY:
        c = settings.execution.celery
        return CeleryPipelineExecutionBackend(
            app=celery_app, pipeline_queue=c.pipeline_queue
        )
    if backend == ExecutionBackend.AIRFLOW:
        a = settings.execution.airflow
        return AirflowPipelineExecutionBackend(
            base_url=a.base_url,
            pipeline_dag_id=a.pipeline_dag_id,
            username=a.username,
            password=a.password,
        )
    raise ValueError(f"Unsupported pipeline execution backend: {backend}")


PipelineExecutionBackendDep = Annotated[
    PipelineExecutionBackend, Depends(get_pipeline_execution_backend)
]


def get_execution_service(
    job_backend: JobExecutionBackendDep,
    pipeline_backend: PipelineExecutionBackendDep,
    record_repository: ExecutionRecordRepositoryDep,
) -> ExecutionService:
    return ExecutionService(
        job_backend=job_backend,
        pipeline_backend=pipeline_backend,
        record_repository=record_repository,
    )


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
