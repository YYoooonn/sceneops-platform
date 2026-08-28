from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.robots.schemas import (
    MissionRecord,
    RobotRecord,
    RobotRunRecord,
    RobotStateRecord,
)
from sceneops_db.postgres import (
    PostgresMissionRepository,
    PostgresRobotRepository,
    PostgresRobotRunRepository,
    PostgresRobotStateRepository,
)


class RobotStore:
    def __init__(self, session: AsyncSession) -> None:
        self._robots = PostgresRobotRepository(session)
        self._runs = PostgresRobotRunRepository(session)
        self._missions = PostgresMissionRepository(session)
        self._states = PostgresRobotStateRepository(session)

    async def get_robot(self, robot_id: str) -> RobotRecord | None:
        return await self._robots.get(robot_id)

    async def upsert_robot(self, robot: RobotRecord) -> RobotRecord:
        return await self._robots.upsert(robot)

    async def get_run(self, run_id: str) -> RobotRunRecord | None:
        return await self._runs.get(run_id)

    async def save_run(self, run: RobotRunRecord) -> RobotRunRecord:
        return await self._runs.update(run)

    async def upsert_run(self, run: RobotRunRecord) -> RobotRunRecord:
        return await self._runs.upsert(run)

    async def get_mission(self, mission_id: str) -> MissionRecord | None:
        return await self._missions.get(mission_id)

    async def upsert_mission(self, mission: MissionRecord) -> MissionRecord:
        return await self._missions.upsert(mission)

    async def create_states(
        self, states: list[RobotStateRecord]
    ) -> list[RobotStateRecord]:
        if not states:
            return []
        return await self._states.create_many(states)

    async def list_states(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[RobotStateRecord]:
        return await self._states.list(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )
