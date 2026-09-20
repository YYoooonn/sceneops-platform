from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_bad_request, raise_not_found
from app.core.pagination import PaginationDep
from app.domains.robots.dependencies import RobotServiceDep
from app.domains.robots.schemas import (
    CreateRobotRequest,
    CreateRobotRunRequest,
    MissionDetailResponse,
    MissionListResponse,
    RobotDetailResponse,
    RobotListResponse,
    RobotRunDetailResponse,
    RobotRunListResponse,
    RobotStateListResponse,
)
from sceneops_core.robots.schemas import MissionStatus, RobotStatus

# Four sibling top-level resources (Robot/RobotRun/Mission/RobotState),
# matching the existing convention that e.g. scenes are a flat top-level
# resource even though every scene belongs to a dataset — filtering by
# parent id is a query param, not URL nesting (see domains/scenes/router.py).

robots_router = APIRouter()
robot_runs_router = APIRouter()
missions_router = APIRouter()
robot_states_router = APIRouter()


# ── Robot ────────────────────────────────────────────────────────────────────


@robots_router.get("", response_model=RobotListResponse)
async def list_robots(
    *,
    service: RobotServiceDep,
    pagination: PaginationDep,
    status: RobotStatus | None = None,
) -> RobotListResponse:
    return await service.list_robots(
        status=status, limit=pagination.limit, offset=pagination.offset
    )


@robots_router.post("", response_model=RobotDetailResponse, status_code=201)
async def create_robot(
    request: CreateRobotRequest, service: RobotServiceDep
) -> RobotDetailResponse:
    return await service.create_robot(request)


@robots_router.get("/{robot_id}", response_model=RobotDetailResponse)
async def get_robot(robot_id: str, service: RobotServiceDep) -> RobotDetailResponse:
    result = await service.get_robot(robot_id)
    if result is None:
        raise_not_found("Robot", robot_id)
    return result


# ── RobotRun ─────────────────────────────────────────────────────────────────


@robot_runs_router.get("", response_model=RobotRunListResponse)
async def list_robot_runs(
    *,
    service: RobotServiceDep,
    pagination: PaginationDep,
    robot_id: str | None = None,
) -> RobotRunListResponse:
    return await service.list_robot_runs(
        robot_id=robot_id, limit=pagination.limit, offset=pagination.offset
    )


@robot_runs_router.post("", response_model=RobotRunDetailResponse, status_code=201)
async def create_robot_run(
    request: CreateRobotRunRequest, service: RobotServiceDep
) -> RobotRunDetailResponse:
    result = await service.create_robot_run(request)
    if result is None:
        raise_bad_request(f"Robot not found: {request.robot_id}")
    return result


@robot_runs_router.get("/{run_id}", response_model=RobotRunDetailResponse)
async def get_robot_run(
    run_id: str, service: RobotServiceDep
) -> RobotRunDetailResponse:
    result = await service.get_robot_run(run_id)
    if result is None:
        raise_not_found("RobotRun", run_id)
    return result


# ── Mission (read-only — written by ingest_robot_states job) ────────────────


@missions_router.get("", response_model=MissionListResponse)
async def list_missions(
    *,
    service: RobotServiceDep,
    pagination: PaginationDep,
    robot_id: str | None = None,
    robot_run_id: str | None = None,
    status: MissionStatus | None = None,
) -> MissionListResponse:
    return await service.list_missions(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        status=status,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@missions_router.get("/{mission_id}", response_model=MissionDetailResponse)
async def get_mission(
    mission_id: str, service: RobotServiceDep
) -> MissionDetailResponse:
    result = await service.get_mission(mission_id)
    if result is None:
        raise_not_found("Mission", mission_id)
    return result


# ── RobotState (read-only — written by ingest_robot_states job) ────────────


@robot_states_router.get("", response_model=RobotStateListResponse)
async def list_robot_states(
    *,
    service: RobotServiceDep,
    pagination: PaginationDep,
    robot_id: str | None = None,
    robot_run_id: str | None = None,
) -> RobotStateListResponse:
    return await service.list_robot_states(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
