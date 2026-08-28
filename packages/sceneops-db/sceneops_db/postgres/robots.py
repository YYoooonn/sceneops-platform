from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.robots.schemas import (
    MissionRecord,
    MissionStatus,
    RobotRecord,
    RobotRunRecord,
    RobotRunStatus,
    RobotStateRecord,
    RobotStatus,
)

from sceneops_db.converters.robots import (
    mission_model_to_record,
    mission_record_to_values,
    robot_model_to_record,
    robot_record_to_values,
    robot_run_model_to_record,
    robot_run_record_to_values,
    robot_state_model_to_record,
    robot_state_record_to_values,
)
from sceneops_db.models.robots import (
    MissionModel,
    RobotModel,
    RobotRunModel,
    RobotStateModel,
)

from ._utils import apply_pagination, apply_values, enum_value


class PostgresRobotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, robot: RobotRecord) -> RobotRecord:
        model = RobotModel(**robot_record_to_values(robot))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return robot_model_to_record(model)

    async def upsert(self, robot: RobotRecord) -> RobotRecord:
        existing = await self.get(robot.robot_id)
        if existing is None:
            return await self.create(robot)
        return await self.update(robot)

    async def get(self, robot_id: str) -> RobotRecord | None:
        stmt = select(RobotModel).where(RobotModel.robot_id == robot_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return robot_model_to_record(model) if model is not None else None

    async def update(self, robot: RobotRecord) -> RobotRecord:
        stmt = select(RobotModel).where(RobotModel.robot_id == robot.robot_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Robot not found: {robot.robot_id}")
        apply_values(model, robot_record_to_values(robot))
        await self._session.flush()
        await self._session.refresh(model)
        return robot_model_to_record(model)

    async def list(
        self,
        *,
        status: RobotStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RobotRecord]:
        stmt = select(RobotModel)
        if status is not None:
            stmt = stmt.where(RobotModel.status == enum_value(status))
        stmt = apply_pagination(
            stmt.order_by(RobotModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [robot_model_to_record(m) for m in result.scalars().all()]


class PostgresRobotRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: RobotRunRecord) -> RobotRunRecord:
        model = RobotRunModel(**robot_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return robot_run_model_to_record(model)

    async def upsert(self, run: RobotRunRecord) -> RobotRunRecord:
        existing = await self.get(run.run_id)
        if existing is None:
            return await self.create(run)
        return await self.update(run)

    async def get(self, run_id: str) -> RobotRunRecord | None:
        stmt = select(RobotRunModel).where(RobotRunModel.run_id == run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return robot_run_model_to_record(model) if model is not None else None

    async def update(self, run: RobotRunRecord) -> RobotRunRecord:
        stmt = select(RobotRunModel).where(RobotRunModel.run_id == run.run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"RobotRun not found: {run.run_id}")
        apply_values(model, robot_run_record_to_values(run))
        await self._session.flush()
        await self._session.refresh(model)
        return robot_run_model_to_record(model)

    async def list(
        self,
        *,
        robot_id: str | None = None,
        status: RobotRunStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RobotRunRecord]:
        stmt = select(RobotRunModel)
        if robot_id is not None:
            stmt = stmt.where(RobotRunModel.robot_id == robot_id)
        if status is not None:
            stmt = stmt.where(RobotRunModel.status == enum_value(status))
        stmt = apply_pagination(
            stmt.order_by(RobotRunModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [robot_run_model_to_record(m) for m in result.scalars().all()]


class PostgresMissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, mission: MissionRecord) -> MissionRecord:
        model = MissionModel(**mission_record_to_values(mission))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mission_model_to_record(model)

    async def upsert(self, mission: MissionRecord) -> MissionRecord:
        existing = await self.get(mission.mission_id)
        if existing is None:
            return await self.create(mission)
        return await self.update(mission)

    async def get(self, mission_id: str) -> MissionRecord | None:
        stmt = select(MissionModel).where(MissionModel.mission_id == mission_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return mission_model_to_record(model) if model is not None else None

    async def update(self, mission: MissionRecord) -> MissionRecord:
        stmt = select(MissionModel).where(MissionModel.mission_id == mission.mission_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Mission not found: {mission.mission_id}")
        apply_values(model, mission_record_to_values(mission))
        await self._session.flush()
        await self._session.refresh(model)
        return mission_model_to_record(model)

    async def list(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        status: MissionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MissionRecord]:
        stmt = select(MissionModel)
        if robot_id is not None:
            stmt = stmt.where(MissionModel.robot_id == robot_id)
        if robot_run_id is not None:
            stmt = stmt.where(MissionModel.robot_run_id == robot_run_id)
        if status is not None:
            stmt = stmt.where(MissionModel.status == enum_value(status))
        stmt = apply_pagination(
            stmt.order_by(MissionModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [mission_model_to_record(m) for m in result.scalars().all()]


class PostgresRobotStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, state: RobotStateRecord) -> RobotStateRecord:
        model = RobotStateModel(**robot_state_record_to_values(state))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return robot_state_model_to_record(model)

    async def create_many(
        self, states: list[RobotStateRecord]
    ) -> list[RobotStateRecord]:
        models = [RobotStateModel(**robot_state_record_to_values(s)) for s in states]
        self._session.add_all(models)
        await self._session.flush()
        for model in models:
            await self._session.refresh(model)
        return [robot_state_model_to_record(m) for m in models]

    async def get(self, state_id: str) -> RobotStateRecord | None:
        stmt = select(RobotStateModel).where(RobotStateModel.state_id == state_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return robot_state_model_to_record(model) if model is not None else None

    async def list(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        mission_id: str | None = None,
        scene_id: str | None = None,
        start_timestamp_us: int | None = None,
        end_timestamp_us: int | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[RobotStateRecord]:
        stmt = select(RobotStateModel)
        if robot_id is not None:
            stmt = stmt.where(RobotStateModel.robot_id == robot_id)
        if robot_run_id is not None:
            stmt = stmt.where(RobotStateModel.robot_run_id == robot_run_id)
        if mission_id is not None:
            stmt = stmt.where(RobotStateModel.mission_id == mission_id)
        if scene_id is not None:
            stmt = stmt.where(RobotStateModel.scene_id == scene_id)
        if start_timestamp_us is not None:
            stmt = stmt.where(RobotStateModel.timestamp_us >= start_timestamp_us)
        if end_timestamp_us is not None:
            stmt = stmt.where(RobotStateModel.timestamp_us <= end_timestamp_us)
        stmt = apply_pagination(
            stmt.order_by(RobotStateModel.timestamp_us.asc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [robot_state_model_to_record(m) for m in result.scalars().all()]
