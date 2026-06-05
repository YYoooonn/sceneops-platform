from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineType,
)

from sceneops_db.converters.pipelines import (
    pipeline_run_manifest_to_values,
    pipeline_run_model_to_manifest,
    pipeline_step_run_manifest_to_values,
    pipeline_step_run_model_to_manifest,
)
from sceneops_db.models.pipelines import PipelineRunModel, PipelineStepRunModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresPipelineRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: PipelineRunManifest) -> PipelineRunManifest:
        model = PipelineRunModel(**pipeline_run_manifest_to_values(run))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return pipeline_run_model_to_manifest(model)

    async def get(self, pipeline_run_id: str) -> PipelineRunManifest | None:
        stmt = select(PipelineRunModel).where(
            PipelineRunModel.pipeline_run_id == pipeline_run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return pipeline_run_model_to_manifest(model) if model is not None else None

    async def update(self, run: PipelineRunManifest) -> PipelineRunManifest:
        stmt = select(PipelineRunModel).where(
            PipelineRunModel.pipeline_run_id == run.pipeline_run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"PipelineRun not found: {run.pipeline_run_id}")
        apply_values(model, pipeline_run_manifest_to_values(run))
        await self._session.flush()
        await self._session.refresh(model)
        return pipeline_run_model_to_manifest(model)

    async def list(
        self,
        *,
        type: PipelineType | None = None,
        status: PipelineRunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineRunManifest]:
        stmt = select(PipelineRunModel)
        if type is not None:
            stmt = stmt.where(PipelineRunModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(PipelineRunModel.status == enum_value(status))
        if dataset_id is not None:
            stmt = stmt.where(PipelineRunModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(PipelineRunModel.dataset_version == dataset_version)
        if model_id is not None:
            stmt = stmt.where(PipelineRunModel.model_id == model_id)
        if model_version is not None:
            stmt = stmt.where(PipelineRunModel.model_version == model_version)
        stmt = apply_pagination(
            stmt.order_by(PipelineRunModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [pipeline_run_model_to_manifest(m) for m in result.scalars().all()]

    async def count_by_status(self) -> dict[str, int]:
        stmt = select(PipelineRunModel.status, func.count()).group_by(
            PipelineRunModel.status
        )
        result = await self._session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}


class PostgresPipelineStepRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, step: PipelineStepRunManifest) -> PipelineStepRunManifest:
        model = PipelineStepRunModel(**pipeline_step_run_manifest_to_values(step))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return pipeline_step_run_model_to_manifest(model)

    async def get(
        self,
        pipeline_step_run_id: str,
    ) -> PipelineStepRunManifest | None:
        stmt = select(PipelineStepRunModel).where(
            PipelineStepRunModel.pipeline_step_run_id == pipeline_step_run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return pipeline_step_run_model_to_manifest(model) if model is not None else None

    async def update(self, step: PipelineStepRunManifest) -> PipelineStepRunManifest:
        stmt = select(PipelineStepRunModel).where(
            PipelineStepRunModel.pipeline_step_run_id == step.pipeline_step_run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"PipelineStepRun not found: {step.pipeline_step_run_id}")
        apply_values(model, pipeline_step_run_manifest_to_values(step))
        await self._session.flush()
        await self._session.refresh(model)
        return pipeline_step_run_model_to_manifest(model)

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineStepRunManifest]:
        stmt = select(PipelineStepRunModel).where(
            PipelineStepRunModel.pipeline_run_id == pipeline_run_id
        )
        stmt = apply_pagination(
            stmt.order_by(PipelineStepRunModel.step_order.asc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [pipeline_step_run_model_to_manifest(m) for m in result.scalars().all()]

    async def get_by_step_id(
        self,
        *,
        pipeline_run_id: str,
        step_id: str,
    ) -> PipelineStepRunManifest | None:
        stmt = select(PipelineStepRunModel).where(
            PipelineStepRunModel.pipeline_run_id == pipeline_run_id,
            PipelineStepRunModel.pipeline_step_id == step_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return pipeline_step_run_model_to_manifest(model) if model is not None else None
