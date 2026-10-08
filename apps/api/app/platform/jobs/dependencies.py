from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import (
    ArtifactRepositoryDep,
    EpisodeRepositoryDep,
    JobEventRepositoryDep,
    JobRepositoryDep,
)
from app.platform.executions.dependencies import JobExecutionBackendDep
from sceneops_execution.jobs.dispatch_facade import JobDispatchFacade
from sceneops_execution.jobs.service import JobService
from sceneops_db.session import get_async_sessionmaker


def get_job_service(
    repository: JobRepositoryDep,
    event_repository: JobEventRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
    episode_repository: EpisodeRepositoryDep,
    settings: ApiSettingsDep,
) -> JobService:
    return JobService(
        repository=repository,
        event_repository=event_repository,
        artifact_repository=artifact_repository,
        episode_repository=episode_repository,
    )


JobServiceDep = Annotated[JobService, Depends(get_job_service)]


def get_job_dispatch_facade(
    settings: ApiSettingsDep,
    job_backend: JobExecutionBackendDep,
) -> JobDispatchFacade:
    return JobDispatchFacade(
        session_factory=get_async_sessionmaker(),
        job_backend=job_backend,
    )


JobDispatchFacadeDep = Annotated[JobDispatchFacade, Depends(get_job_dispatch_facade)]
