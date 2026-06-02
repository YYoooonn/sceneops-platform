from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep, DbSessionDep
from app.modules.jobs.service import JobService
from sceneops_db.jobs import (
    JobEventRepository,
    JobRepository,
    PostgresJobEventRepository,
    PostgresJobRepository,
)


def get_job_repository(
    session: DbSessionDep,
) -> JobRepository:
    return PostgresJobRepository(session)


JobRepositoryDep = Annotated[
    JobRepository,
    Depends(get_job_repository),
]


def get_job_event_repository(
    session: DbSessionDep,
) -> JobEventRepository:
    return PostgresJobEventRepository(session)


JobEventRepositoryDep = Annotated[
    JobEventRepository,
    Depends(get_job_event_repository),
]


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


JobServiceDep = Annotated[
    JobService,
    Depends(get_job_service),
]
