from __future__ import annotations

from typing import Annotated

from celery import Celery
from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import ExecutionRecordRepositoryDep
from sceneops_execution.executions.backends import (
    CeleryJobExecutionBackend,
    CeleryPipelineExecutionBackend,
    JobExecutionBackend,
    PipelineExecutionBackend,
)
from sceneops_execution.executions.client import create_celery_client
from sceneops_execution.executions.service import ExecutionService


def get_celery_app(settings: ApiSettingsDep) -> Celery:
    c = settings.execution.celery
    return create_celery_client(
        broker_url=c.broker_url, result_backend=c.result_backend
    )


CeleryAppDep = Annotated[Celery, Depends(get_celery_app)]


def get_job_execution_backend(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> JobExecutionBackend:
    return CeleryJobExecutionBackend(
        app=celery_app, job_queue=settings.execution.celery.job_queue
    )


JobExecutionBackendDep = Annotated[
    JobExecutionBackend, Depends(get_job_execution_backend)
]


def get_pipeline_execution_backend(
    settings: ApiSettingsDep,
    celery_app: CeleryAppDep,
) -> PipelineExecutionBackend:
    return CeleryPipelineExecutionBackend(
        app=celery_app, pipeline_queue=settings.execution.celery.pipeline_queue
    )


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
