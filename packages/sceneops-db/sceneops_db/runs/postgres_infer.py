from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.runs import (
    InferenceRunRecord,
    RunStatus,
)
from sceneops_db.runs.models import InferenceRunModel
from sceneops_db.utils import enum_to_str, to_error_json, to_jsonable


class PostgresInferenceRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, record: InferenceRunRecord) -> InferenceRunRecord:
        status = enum_to_str(record.status)

        stmt = (
            insert(InferenceRunModel)
            .values(
                id=record.id,
                dataset_id=record.dataset_id,
                dataset_version=record.dataset_version,
                model_id=record.model_id,
                model_version=record.model_version,
                status=status,
                sample_count=record.sample_count,
                prediction_count=record.prediction_count,
                run_manifest_uri=record.run_manifest_uri,
                predictions_root_uri=record.predictions_root_uri,
                pipeline_run_id=record.pipeline_run_id,
                pipeline_step_run_id=record.pipeline_step_run_id,
                job_id=record.job_id,
                metrics=to_jsonable(record.metrics) or {},
                metadata_=to_jsonable(record.metadata) or {},
                error=to_error_json(record.error),
                started_at=record.started_at,
                finished_at=record.finished_at,
            )
            .on_conflict_do_update(
                index_elements=[InferenceRunModel.id],
                set_={
                    "dataset_id": record.dataset_id,
                    "dataset_version": record.dataset_version,
                    "model_id": record.model_id,
                    "model_version": record.model_version,
                    "status": status,
                    "sample_count": record.sample_count,
                    "prediction_count": record.prediction_count,
                    "run_manifest_uri": record.run_manifest_uri,
                    "predictions_root_uri": record.predictions_root_uri,
                    "pipeline_run_id": record.pipeline_run_id,
                    "pipeline_step_run_id": record.pipeline_step_run_id,
                    "job_id": record.job_id,
                    "metrics": to_jsonable(record.metrics) or {},
                    "metadata": to_jsonable(record.metadata) or {},
                    "error": to_error_json(record.error),
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                },
            )
            .returning(InferenceRunModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()
        await self.session.commit()
        await self.session.refresh(model)
        return self._to_schema(model)

    async def get(self, run_id: str) -> InferenceRunRecord:
        model = await self.session.get(InferenceRunModel, run_id)
        if model is None:
            raise FileNotFoundError(f"Inference run not found: {run_id}")
        return self._to_schema(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: RunStatus | None = None,
    ) -> list[InferenceRunRecord]:
        stmt = select(InferenceRunModel).order_by(InferenceRunModel.created_at.desc())

        if dataset_id is not None:
            stmt = stmt.where(InferenceRunModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(InferenceRunModel.dataset_version == dataset_version)
        if model_id is not None:
            stmt = stmt.where(InferenceRunModel.model_id == model_id)
        if model_version is not None:
            stmt = stmt.where(InferenceRunModel.model_version == model_version)
        if status is not None:
            stmt = stmt.where(InferenceRunModel.status == enum_to_str(status))

        result = await self.session.execute(stmt)
        return [self._to_schema(model) for model in result.scalars().all()]

    def _to_schema(self, model: InferenceRunModel) -> InferenceRunRecord:
        return InferenceRunRecord.model_validate(
            {
                "id": model.id,
                "dataset_id": model.dataset_id,
                "dataset_version": model.dataset_version,
                "model_id": model.model_id,
                "model_version": model.model_version,
                "status": model.status,
                "sample_count": model.sample_count,
                "prediction_count": model.prediction_count,
                "run_manifest_uri": model.run_manifest_uri,
                "predictions_root_uri": model.predictions_root_uri,
                "pipeline_run_id": model.pipeline_run_id,
                "pipeline_step_run_id": model.pipeline_step_run_id,
                "job_id": model.job_id,
                "metrics": model.metrics or {},
                "metadata": model.metadata_ or {},
                "error": model.error,
                "created_at": model.created_at,
                "updated_at": model.updated_at,
                "started_at": model.started_at,
                "finished_at": model.finished_at,
            }
        )
