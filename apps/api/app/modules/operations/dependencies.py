from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.jobs.dependencies import JobEventRepositoryDep, JobRepositoryDep
from app.modules.operations.service import OperationsService
from app.modules.pipelines.dependencies import (
    PipelineRunRepositoryDep,
    PipelineStepRunRepositoryDep,
)


def get_operations_service(
    job_repository: JobRepositoryDep,
    job_event_repository: JobEventRepositoryDep,
    pipeline_run_repository: PipelineRunRepositoryDep,
    pipeline_step_run_repository: PipelineStepRunRepositoryDep,
) -> OperationsService:
    return OperationsService(
        job_repository=job_repository,
        job_event_repository=job_event_repository,
        pipeline_run_repository=pipeline_run_repository,
        pipeline_step_run_repository=pipeline_step_run_repository,
    )


OperationsServiceDep = Annotated[
    OperationsService,
    Depends(get_operations_service),
]
