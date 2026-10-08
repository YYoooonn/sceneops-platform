from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from app.core.repositories import (
    MissionRepositoryDep,
    RobotRepositoryDep,
    RobotRunRepositoryDep,
    RobotStateRepositoryDep,
)
from app.domains.robots.service import RobotService
from app.platform.jobs.dependencies import JobDispatchFacadeDep
from sceneops_acquisition.registration import (
    RobotRunRegistrationService,
    create_registration_service,
)
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
    dispatch_facade: JobDispatchFacadeDep,
) -> RobotRunRegistrationService:
    return create_registration_service(
        session_factory=get_async_sessionmaker(),
        dispatch_facade=dispatch_facade,
    )


RobotRunRegistrationServiceDep = Annotated[
    RobotRunRegistrationService, Depends(get_robot_run_registration_service)
]
