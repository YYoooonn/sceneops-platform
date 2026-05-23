from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.pipelines import (
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_db.utils import extract_datetime, enum_to_str, to_error_info
from sceneops_db.pipelines.models import PipelineStepRunModel


class PostgresPipelineStepRunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, manifest: PipelineStepRunManifest) -> PipelineStepRunManifest:
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
        model = await self.session.get(PipelineStepRunModel, manifest.pipelineStepRunId)

        if model is None:
            raise FileNotFoundError(
                f"Pipeline step run not found: {manifest.pipelineStepRunId}"
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
        data = manifest.model_dump(mode="json")

        return PipelineStepRunModel(
            id=data["pipelineStepRunId"],
            pipeline_run_id=data["pipelineRunId"],
            step_name=data["stepName"],
            step_order=data["stepOrder"],
            status=enum_to_str(data["status"]),
            job_type=enum_to_str(data["jobType"]),
            job_id=data.get("jobId"),
            depends_on_step_names=data.get("dependsOnStepNames") or [],
            params=data.get("params") or {},
            result=data.get("result"),
            error=data.get("error"),
            started_at=extract_datetime(data.get("startedAt")),
            finished_at=extract_datetime(data.get("finishedAt")),
        )

    def _to_schema(self, model: PipelineStepRunModel) -> PipelineStepRunManifest:
        return PipelineStepRunManifest(
            pipelineStepRunId=model.id,
            pipelineRunId=model.pipeline_run_id,
            stepName=model.step_name,
            stepOrder=model.step_order,
            status=PipelineStepRunStatus(model.status),
            jobType=model.job_type,
            jobId=model.job_id,
            dependsOnStepNames=model.depends_on_step_names or [],
            params=model.params or {},
            result=model.result,
            error=to_error_info(model.error),
            createdAt=model.created_at.isoformat(),
            updatedAt=model.updated_at.isoformat(),
            startedAt=model.started_at.isoformat() if model.started_at else None,
            finishedAt=model.finished_at.isoformat() if model.finished_at else None,
        )
