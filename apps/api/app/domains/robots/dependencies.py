from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import (
    MissionRepositoryDep,
    RobotRepositoryDep,
    RobotRunRepositoryDep,
    RobotStateRepositoryDep,
)
from app.domains.robots.registration import RobotRunRegistrationService
from app.domains.robots.service import RobotService
from app.platform.jobs.dependencies import JobDispatchFacadeDep
from app.platform.jobs.service import JobService
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker


def get_robot_service(
    robot_repository: RobotRepositoryDep,
    robot_run_repository: RobotRunRepositoryDep,
    mission_repository: MissionRepositoryDep,
    robot_state_repository: RobotStateRepositoryDep,
) -> RobotService:
    return RobotService(
        robot_repository=robot_repository,
        robot_run_repository=robot_run_repository,
        mission_repository=mission_repository,
        robot_state_repository=robot_state_repository,
    )


RobotServiceDep = Annotated[RobotService, Depends(get_robot_service)]


def get_robot_run_registration_service(
    settings: ApiSettingsDep,
    dispatch_facade: JobDispatchFacadeDep,
) -> RobotRunRegistrationService:
    def _job_service(session: AsyncSession) -> JobService:
        return JobService(
            repository=PostgresJobRepository(session),
            event_repository=PostgresJobEventRepository(session),
            artifact_repository=PostgresArtifactRefRepository(session),
            default_dataset_id=settings.default_dataset_id,
            default_dataset_version=settings.default_dataset_version,
        )

    return RobotRunRegistrationService(
        session_factory=get_async_sessionmaker(),
        job_service_factory=_job_service,
        dispatch_facade=dispatch_facade,
    )


RobotRunRegistrationServiceDep = Annotated[
    RobotRunRegistrationService, Depends(get_robot_run_registration_service)
]
