from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import JobEventRepositoryDep, JobRepositoryDep
from app.platform.executions.dependencies import ExecutionBackendDep
from app.platform.jobs.dispatch_facade import JobDispatchFacade
from app.platform.jobs.service import JobService
from sceneops_db.session import get_async_sessionmaker


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


def get_job_dispatch_facade(
    settings: ApiSettingsDep,
    backend: ExecutionBackendDep,
) -> JobDispatchFacade:
    return JobDispatchFacade(
        session_factory=get_async_sessionmaker(),
        backend=backend,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


JobDispatchFacadeDep = Annotated[JobDispatchFacade, Depends(get_job_dispatch_facade)]
