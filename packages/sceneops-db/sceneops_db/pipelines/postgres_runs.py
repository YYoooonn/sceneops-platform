from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.pipelines import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_db.utils import enum_to_str, extract_datetime, to_error_info
from sceneops_db.pipelines.models import PipelineRunModel


class PostgresPipelineRunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, manifest: PipelineRunManifest) -> PipelineRunManifest:
        model = self._to_model(manifest)

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(self, pipeline_run_id: str) -> PipelineRunManifest:
        model = await self.session.get(PipelineRunModel, pipeline_run_id)

        if model is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")

        return self._to_schema(model)

    async def list(
        self,
        *,
        status: PipelineRunStatus | None = None,
        pipeline_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[PipelineRunManifest]:
        stmt = select(PipelineRunModel)

        if status is not None:
            stmt = stmt.where(PipelineRunModel.status == enum_to_str(status))

        if pipeline_type is not None:
            stmt = stmt.where(PipelineRunModel.type == pipeline_type)

        if dataset_id is not None:
            stmt = stmt.where(PipelineRunModel.dataset_id == dataset_id)

        if dataset_version is not None:
            stmt = stmt.where(PipelineRunModel.dataset_version == dataset_version)

        stmt = stmt.order_by(PipelineRunModel.created_at.desc())

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update(self, manifest: PipelineRunManifest) -> PipelineRunManifest:
        model = await self.session.get(PipelineRunModel, manifest.pipeline_run_id)

        if model is None:
            raise FileNotFoundError(
                f"Pipeline run not found: {manifest.pipeline_run_id}"
            )

        updated = self._to_model(manifest)

        model.type = updated.type
        model.status = updated.status
        model.dataset_id = updated.dataset_id
        model.dataset_version = updated.dataset_version
        model.model_id = updated.model_id
        model.model_version = updated.model_version
        model.params = updated.params
        model.result = updated.result
        model.error = updated.error
        model.started_at = updated.started_at
        model.finished_at = updated.finished_at

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    def _to_model(self, manifest: PipelineRunManifest) -> PipelineRunModel:

        result = manifest.result.to_db_dict() if manifest.result else None
        params = manifest.params if isinstance(manifest.params, dict) else {}
        error = manifest.error if isinstance(manifest.error, dict) else None

        return PipelineRunModel(
            id=manifest.pipeline_run_id,
            type=enum_to_str(manifest.type),
            status=enum_to_str(manifest.status),
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            model_id=manifest.model_id,
            model_version=manifest.model_version,
            params=params,
            result=result,
            error=error,
            started_at=extract_datetime(manifest.started_at),
            finished_at=extract_datetime(manifest.finished_at),
        )

    def _to_schema(self, model: PipelineRunModel) -> PipelineRunManifest:
        return PipelineRunManifest.model_validate({
            "pipeline_run_id": model.id,
            "type": model.type,
            "status": model.status,
            "dataset_id": model.dataset_id,
            "dataset_version": model.dataset_version,
            "model_id": model.model_id,
            "model_version": model.model_version,
            "params": model.params or {},
            "result": model.result,
            "error": to_error_info(model.error),
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "started_at": model.started_at,
            "finished_at": model.finished_at,
        })
