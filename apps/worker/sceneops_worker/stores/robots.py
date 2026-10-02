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

    async def create_robot_if_absent(self, robot: RobotRecord) -> None:
        await self._robots.create_if_absent(robot)

    async def get_robot_for_update(self, robot_id: str) -> RobotRecord | None:
        return await self._robots.get_for_update(robot_id)

    async def save_robot(self, robot: RobotRecord) -> RobotRecord:
        return await self._robots.update(robot)

    async def get_run(self, run_id: str) -> RobotRunRecord | None:
        return await self._runs.get(run_id)

    async def create_run(self, run: RobotRunRecord) -> RobotRunRecord:
        """Insert-only: raises IntegrityError on an existing run_id (the
        table's primary key) instead of overwriting it. RobotRunRecords are
        immutable; REGISTER_ROBOT_RUN resolves the race (ADR-007 §12.1 R9)."""
        return await self._runs.create(run)

    async def get_mission(self, mission_id: str) -> MissionRecord | None:
        return await self._missions.get(mission_id)

    async def upsert_mission(self, mission: MissionRecord) -> MissionRecord:
        return await self._missions.upsert(mission)

    async def list_missions(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[MissionRecord]:
        return await self._missions.list(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )

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
