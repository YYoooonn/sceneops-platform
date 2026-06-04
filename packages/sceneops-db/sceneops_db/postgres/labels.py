from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType

from sceneops_db.converters.labels import (
    LabelRunRecord,
    label_run_model_to_record,
    label_run_record_to_values,
)
from sceneops_db.models.labels import LabelRunModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresLabelRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: LabelRunRecord) -> LabelRunRecord:
        model = LabelRunModel(**label_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        return label_run_model_to_record(model)

    async def get(self, run_id: str) -> LabelRunRecord | None:
        stmt = select(LabelRunModel).where(LabelRunModel.run_id == run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return label_run_model_to_record(model) if model is not None else None

    async def update(self, run: LabelRunRecord) -> LabelRunRecord:
        stmt = select(LabelRunModel).where(LabelRunModel.run_id == run.run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"LabelRun not found: {run.run_id}")
        apply_values(model, label_run_record_to_values(run))
        await self._session.flush()
        return label_run_model_to_record(model)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        labeler_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LabelRunRecord]:
        stmt = select(LabelRunModel)
        if type is not None:
            stmt = stmt.where(LabelRunModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(LabelRunModel.status == enum_value(status))
        if dataset_id is not None:
            stmt = stmt.where(LabelRunModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(LabelRunModel.dataset_version == dataset_version)
        if scene_id is not None:
            stmt = stmt.where(LabelRunModel.scene_id == scene_id)
        if labeler_id is not None:
            stmt = stmt.where(LabelRunModel.labeler_id == labeler_id)
        if job_id is not None:
            stmt = stmt.where(LabelRunModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(LabelRunModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(LabelRunModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [label_run_model_to_record(m) for m in result.scalars().all()]
