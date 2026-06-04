from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.runs.schemas import RunStatus

from sceneops_db.converters.inference import (
    inference_run_model_to_record,
    inference_run_record_to_values,
)
from sceneops_db.models.inference import InferenceRunModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresInferenceRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: InferenceRunRecord) -> InferenceRunRecord:
        model = InferenceRunModel(**inference_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        return inference_run_model_to_record(model)

    async def get(self, run_id: str) -> InferenceRunRecord | None:
        stmt = select(InferenceRunModel).where(InferenceRunModel.run_id == run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return inference_run_model_to_record(model) if model is not None else None

    async def update(self, run: InferenceRunRecord) -> InferenceRunRecord:
        stmt = select(InferenceRunModel).where(InferenceRunModel.run_id == run.run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"InferenceRun not found: {run.run_id}")
        apply_values(model, inference_run_record_to_values(run))
        await self._session.flush()
        return inference_run_model_to_record(model)

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InferenceRunRecord]:
        stmt = select(InferenceRunModel)
        if status is not None:
            stmt = stmt.where(InferenceRunModel.status == enum_value(status))
        if dataset_id is not None:
            stmt = stmt.where(InferenceRunModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(InferenceRunModel.dataset_version == dataset_version)
        if model_id is not None:
            stmt = stmt.where(InferenceRunModel.model_id == model_id)
        if model_version is not None:
            stmt = stmt.where(InferenceRunModel.model_version == model_version)
        if job_id is not None:
            stmt = stmt.where(InferenceRunModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(InferenceRunModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(InferenceRunModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [inference_run_model_to_record(m) for m in result.scalars().all()]
