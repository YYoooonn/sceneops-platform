from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.evaluations.schemas import EvaluationTaskType
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.runs.schemas import RunStatus

from sceneops_db.converters.evaluations import (
    evaluation_run_model_to_record,
    evaluation_run_record_to_values,
)
from sceneops_db.models.evaluations import EvaluationRunModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresEvaluationRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        model = EvaluationRunModel(**evaluation_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return evaluation_run_model_to_record(model)

    async def get(self, run_id: str) -> EvaluationRunRecord | None:
        stmt = select(EvaluationRunModel).where(EvaluationRunModel.run_id == run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return evaluation_run_model_to_record(model) if model is not None else None

    async def update(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        stmt = select(EvaluationRunModel).where(EvaluationRunModel.run_id == run.run_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"EvaluationRun not found: {run.run_id}")
        apply_values(model, evaluation_run_record_to_values(run))
        await self._session.flush()
        await self._session.refresh(model)
        return evaluation_run_model_to_record(model)

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        task_type: EvaluationTaskType | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        evaluator_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EvaluationRunRecord]:
        stmt = select(EvaluationRunModel)
        if status is not None:
            stmt = stmt.where(EvaluationRunModel.status == enum_value(status))
        if task_type is not None:
            stmt = stmt.where(EvaluationRunModel.task_type == enum_value(task_type))
        if dataset_id is not None:
            stmt = stmt.where(EvaluationRunModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(EvaluationRunModel.dataset_version == dataset_version)
        if model_id is not None:
            stmt = stmt.where(EvaluationRunModel.model_id == model_id)
        if model_version is not None:
            stmt = stmt.where(EvaluationRunModel.model_version == model_version)
        if inference_run_id is not None:
            stmt = stmt.where(EvaluationRunModel.inference_run_id == inference_run_id)
        if evaluator_id is not None:
            stmt = stmt.where(EvaluationRunModel.evaluator_id == evaluator_id)
        if job_id is not None:
            stmt = stmt.where(EvaluationRunModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(EvaluationRunModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(EvaluationRunModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [evaluation_run_model_to_record(m) for m in result.scalars().all()]
