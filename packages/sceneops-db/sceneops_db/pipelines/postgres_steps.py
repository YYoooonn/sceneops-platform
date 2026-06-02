from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.pipelines.schemas import (
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_db.utils import extract_datetime, enum_to_str, to_error_info
from sceneops_db.pipelines.models import PipelineStepRunModel


class PostgresPipelineStepRunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, manifest: PipelineStepRunManifest
    ) -> PipelineStepRunManifest:
        model = self._to_model(manifest)

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def create_many(
        self,
        manifests: list[PipelineStepRunManifest],
    ) -> list[PipelineStepRunManifest]:
        models = [self._to_model(manifest) for manifest in manifests]

        self.session.add_all(models)
        await self.session.commit()

        for model in models:
            await self.session.refresh(model)

        return [self._to_schema(model) for model in models]

    async def get(self, pipeline_step_run_id: str) -> PipelineStepRunManifest:
        model = await self.session.get(PipelineStepRunModel, pipeline_step_run_id)

        if model is None:
            raise FileNotFoundError(
                f"Pipeline step run not found: {pipeline_step_run_id}"
            )

        return self._to_schema(model)

    async def list_by_pipeline_run(
        self,
        pipeline_run_id: str,
    ) -> list[PipelineStepRunManifest]:
        stmt = (
            select(PipelineStepRunModel)
            .where(PipelineStepRunModel.pipeline_run_id == pipeline_run_id)
            .order_by(PipelineStepRunModel.step_order.asc())
        )

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update(
        self,
        manifest: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest:
        model = await self.session.get(
            PipelineStepRunModel, manifest.pipeline_step_run_id
        )

        if model is None:
            raise FileNotFoundError(
                f"Pipeline step run not found: {manifest.pipeline_step_run_id}"
            )

        updated = self._to_model(manifest)

        model.pipeline_run_id = updated.pipeline_run_id
        model.step_name = updated.step_name
        model.step_order = updated.step_order
        model.status = updated.status
        model.job_type = updated.job_type
        model.job_id = updated.job_id
        model.depends_on_step_names = updated.depends_on_step_names
        model.params = updated.params
        model.result = updated.result
        model.error = updated.error
        model.started_at = updated.started_at
        model.finished_at = updated.finished_at

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def update_status(
        self,
        pipeline_step_run_id: str,
        status: PipelineStepRunStatus,
    ) -> PipelineStepRunManifest:
        model = await self.session.get(PipelineStepRunModel, pipeline_step_run_id)

        if model is None:
            raise FileNotFoundError(
                f"Pipeline step run not found: {pipeline_step_run_id}"
            )

        model.status = enum_to_str(status)

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    def _to_model(self, manifest: PipelineStepRunManifest) -> PipelineStepRunModel:
        result = manifest.result.to_db_dict() if manifest.result else None
        error = manifest.error.to_db_dict() if manifest.error else None
        params = manifest.params if isinstance(manifest.params, dict) else {}

        return PipelineStepRunModel(
            id=manifest.pipeline_step_run_id,
            pipeline_run_id=manifest.pipeline_run_id,
            step_name=manifest.step_name,
            step_order=manifest.step_order,
            status=enum_to_str(manifest.status),
            job_type=enum_to_str(manifest.job_type),
            job_id=manifest.job_id,
            depends_on_step_names=manifest.depends_on_step_names or [],
            params=params,
            result=result,
            error=error,
            started_at=extract_datetime(manifest.started_at),
            finished_at=extract_datetime(manifest.finished_at),
        )

    def _to_schema(self, model: PipelineStepRunModel) -> PipelineStepRunManifest:
        return PipelineStepRunManifest.model_validate(
            {
                "pipeline_step_run_id": model.id,
                "pipeline_run_id": model.pipeline_run_id,
                "step_name": model.step_name,
                "step_order": model.step_order,
                "status": model.status,
                "job_type": model.job_type,
                "job_id": model.job_id,
                "depends_on_step_names": model.depends_on_step_names or [],
                "params": model.params or {},
                "result": model.result,
                "error": to_error_info(model.error),
                "created_at": model.created_at.isoformat(),
                "updated_at": model.updated_at.isoformat(),
                "started_at": model.started_at.isoformat()
                if model.started_at
                else None,
                "finished_at": model.finished_at.isoformat()
                if model.finished_at
                else None,
            }
        )
