from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import (
    ExecutionRecordRepositoryDep,
    JobRepositoryDep,
    PipelineRunRepositoryDep,
)
from app.views.operations.service import OperationsService


def get_operations_service(
    job_repository: JobRepositoryDep,
    pipeline_repository: PipelineRunRepositoryDep,
    execution_repository: ExecutionRecordRepositoryDep,
) -> OperationsService:
    return OperationsService(
        job_repository=job_repository,
        pipeline_repository=pipeline_repository,
        execution_repository=execution_repository,
    )


OperationsServiceDep = Annotated[OperationsService, Depends(get_operations_service)]
