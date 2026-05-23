from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.pipelines import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_db.utils import extract_datetime, enum_to_str, to_error_info
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
        model = await self.session.get(PipelineRunModel, manifest.pipelineRunId)

        if model is None:
            raise FileNotFoundError(
                f"Pipeline run not found: {manifest.pipelineRunId}"
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
        data = manifest.model_dump(mode="json")

        return PipelineRunModel(
            id=data["pipelineRunId"],
            type=enum_to_str(data["type"]),
            status=enum_to_str(data["status"]),
            dataset_id=data["datasetId"],
            dataset_version=data["datasetVersion"],
            model_id=data.get("modelId"),
            model_version=data.get("modelVersion"),
            params=data.get("params") or {},
            result=data.get("result"),
            error=data.get("error"),
            started_at=extract_datetime(data.get("startedAt")),
            finished_at=extract_datetime(data.get("finishedAt")),
        )

    def _to_schema(self, model: PipelineRunModel) -> PipelineRunManifest:
        return PipelineRunManifest(
            pipelineRunId=model.id,
            type=PipelineType(model.type),
            status=PipelineRunStatus(model.status),
            datasetId=model.dataset_id,
            datasetVersion=model.dataset_version,
            modelId=model.model_id,
            modelVersion=model.model_version,
            params=model.params or {},
            result=model.result,
            error=to_error_info(model.error),
            createdAt=model.created_at.isoformat(),
            updatedAt=model.updated_at.isoformat(),
            startedAt=model.started_at.isoformat() if model.started_at else None,
            finishedAt=model.finished_at.isoformat() if model.finished_at else None,
        )
