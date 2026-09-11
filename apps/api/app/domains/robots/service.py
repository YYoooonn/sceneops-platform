from __future__ import annotations

from sceneops_core.robots.schemas import (
    MissionStatus,
    RobotRecord,
    RobotRunRecord,
    RobotStatus,
)
from sceneops_db.repositories.robots import (
    MissionRepository,
    RobotRepository,
    RobotRunRepository,
    RobotStateRepository,
)

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


class RobotService:
    def __init__(
        self,
        *,
        robot_repository: RobotRepository,
        robot_run_repository: RobotRunRepository,
        mission_repository: MissionRepository,
        robot_state_repository: RobotStateRepository,
    ) -> None:
        self._robots = robot_repository
        self._robot_runs = robot_run_repository
        self._missions = mission_repository
        self._robot_states = robot_state_repository

    # ── Robot ────────────────────────────────────────────────────────────────

    async def list_robots(
        self,
        *,
        status: RobotStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> RobotListResponse:
        robots = await self._robots.list(status=status, limit=limit, offset=offset)
        return RobotListResponse(robots=robots, count=len(robots))

    async def create_robot(self, request: CreateRobotRequest) -> RobotDetailResponse:
        robot = await self._robots.upsert(
            RobotRecord(
                robot_id=request.robot_id,
                name=request.name,
                platform=request.platform,
                metadata=request.metadata,
            )
        )
        return RobotDetailResponse(robot=robot)

    async def get_robot(self, robot_id: str) -> RobotDetailResponse | None:
        robot = await self._robots.get(robot_id)
        if robot is None:
            return None
        return RobotDetailResponse(robot=robot)

    # ── RobotRun ─────────────────────────────────────────────────────────────

    async def list_robot_runs(
        self,
        *,
        robot_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> RobotRunListResponse:
        runs = await self._robot_runs.list(
            robot_id=robot_id, limit=limit, offset=offset
        )
        return RobotRunListResponse(robot_runs=runs, count=len(runs))

    async def create_robot_run(
        self, request: CreateRobotRunRequest
    ) -> RobotRunDetailResponse | None:
        """Returns None if request.robot_id doesn't exist — caller 404s."""
        robot = await self._robots.get(request.robot_id)
        if robot is None:
            return None
        robot_run = await self._robot_runs.upsert(
            RobotRunRecord(
                run_id=request.run_id,
                robot_id=request.robot_id,
                mcap_uri=request.mcap_uri,
                rosbag_uri=request.rosbag_uri,
                metadata=request.metadata,
            )
        )
        return RobotRunDetailResponse(robot_run=robot_run)

    async def get_robot_run(self, run_id: str) -> RobotRunDetailResponse | None:
        robot_run = await self._robot_runs.get(run_id)
        if robot_run is None:
            return None
        return RobotRunDetailResponse(robot_run=robot_run)

    # ── Mission (read-only — written by ingest_robot_states job) ─────────────

    async def list_missions(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        status: MissionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> MissionListResponse:
        missions = await self._missions.list(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return MissionListResponse(missions=missions, count=len(missions))

    async def get_mission(self, mission_id: str) -> MissionDetailResponse | None:
        mission = await self._missions.get(mission_id)
        if mission is None:
            return None
        return MissionDetailResponse(mission=mission)

    # ── RobotState (read-only — written by ingest_robot_states job) ─────────

    async def list_robot_states(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> RobotStateListResponse:
        states = await self._robot_states.list(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )
        return RobotStateListResponse(robot_states=states, count=len(states))
