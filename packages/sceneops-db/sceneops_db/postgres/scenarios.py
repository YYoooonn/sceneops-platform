from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord

from sceneops_db.converters.scenarios import (
    ScenarioRunRecord,
    scenario_run_model_to_record,
    scenario_run_record_to_values,
    scenario_set_model_to_record,
    scenario_set_record_to_values,
)
from sceneops_db.models.scenarios import ScenarioRunRecordModel, ScenarioSetModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresScenarioSetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: ScenarioSetRecord) -> ScenarioSetRecord:
        model = ScenarioSetModel(**scenario_set_record_to_values(record))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return scenario_set_model_to_record(model)

    async def upsert(self, record: ScenarioSetRecord) -> ScenarioSetRecord:
        existing = await self.get(record.scenario_set_id)
        if existing is None:
            return await self.create(record)
        return await self.update(record)

    async def get(self, scenario_set_id: str) -> ScenarioSetRecord | None:
        stmt = select(ScenarioSetModel).where(
            ScenarioSetModel.scenario_set_id == scenario_set_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return scenario_set_model_to_record(model) if model is not None else None

    async def update(self, record: ScenarioSetRecord) -> ScenarioSetRecord:
        stmt = select(ScenarioSetModel).where(
            ScenarioSetModel.scenario_set_id == record.scenario_set_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"ScenarioSet not found: {record.scenario_set_id}")
        apply_values(model, scenario_set_record_to_values(record))
        await self._session.flush()
        return scenario_set_model_to_record(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScenarioSetRecord]:
        stmt = select(ScenarioSetModel)
        if dataset_id is not None:
            stmt = stmt.where(ScenarioSetModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(ScenarioSetModel.dataset_version == dataset_version)
        stmt = apply_pagination(
            stmt.order_by(ScenarioSetModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [scenario_set_model_to_record(m) for m in result.scalars().all()]


class PostgresScenarioRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: ScenarioRunRecord) -> ScenarioRunRecord:
        model = ScenarioRunRecordModel(**scenario_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        return scenario_run_model_to_record(model)

    async def get(self, run_id: str) -> ScenarioRunRecord | None:
        stmt = select(ScenarioRunRecordModel).where(
            ScenarioRunRecordModel.run_id == run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return scenario_run_model_to_record(model) if model is not None else None

    async def update(self, run: ScenarioRunRecord) -> ScenarioRunRecord:
        stmt = select(ScenarioRunRecordModel).where(
            ScenarioRunRecordModel.run_id == run.run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"ScenarioRun not found: {run.run_id}")
        apply_values(model, scenario_run_record_to_values(run))
        await self._session.flush()
        return scenario_run_model_to_record(model)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scenario_set_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScenarioRunRecord]:
        stmt = select(ScenarioRunRecordModel)
        if type is not None:
            stmt = stmt.where(ScenarioRunRecordModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(ScenarioRunRecordModel.status == enum_value(status))
        if scenario_set_id is not None:
            stmt = stmt.where(ScenarioRunRecordModel.scenario_set_id == scenario_set_id)
        if dataset_id is not None:
            stmt = stmt.where(ScenarioRunRecordModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(ScenarioRunRecordModel.dataset_version == dataset_version)
        if job_id is not None:
            stmt = stmt.where(ScenarioRunRecordModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(ScenarioRunRecordModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(ScenarioRunRecordModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [scenario_run_model_to_record(m) for m in result.scalars().all()]
