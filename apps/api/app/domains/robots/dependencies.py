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
