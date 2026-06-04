from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import JobEventRepositoryDep, JobRepositoryDep
from app.platform.jobs.service import JobService


def get_job_service(
    repository: JobRepositoryDep,
    event_repository: JobEventRepositoryDep,
    settings: ApiSettingsDep,
) -> JobService:
    return JobService(
        repository=repository,
        event_repository=event_repository,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


JobServiceDep = Annotated[JobService, Depends(get_job_service)]
