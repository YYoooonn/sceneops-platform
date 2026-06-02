from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import AutoLabelRunRecord, RunStatus
from sceneops_db.runs.models import AutoLabelRunModel
from sceneops_db.utils import enum_to_str, to_error_json, to_jsonable


class PostgresAutoLabelRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        record: AutoLabelRunRecord,
    ) -> AutoLabelRunRecord:
        status = enum_to_str(record.status)

        metrics = to_jsonable(record.metrics) or {}
        class_metrics = to_jsonable(record.class_metrics) or {}
        metadata = to_jsonable(record.metadata) or {}
        error = to_error_json(record.error)

        stmt = (
            insert(AutoLabelRunModel)
            .values(
                id=record.id,
                dataset_id=record.dataset_id,
                dataset_version=record.dataset_version,
                model_id=record.model_id,
                model_version=record.model_version,
                vlm_backend=enum_to_str(record.vlm_backend),
                status=status,
                sample_count=record.sample_count,
                labeled_sample_count=record.labeled_sample_count,
                auto_label_manifest_uri=record.auto_label_manifest_uri,
                samples_root_uri=record.samples_root_uri,
                metrics=metrics,
                class_metrics=class_metrics,
                pipeline_run_id=record.pipeline_run_id,
                pipeline_step_run_id=record.pipeline_step_run_id,
                job_id=record.job_id,
                metadata_=metadata,
                error=error,
                started_at=record.started_at,
                finished_at=record.finished_at,
            )
            .on_conflict_do_update(
                index_elements=[AutoLabelRunModel.id],
                set_={
                    "dataset_id": record.dataset_id,
                    "dataset_version": record.dataset_version,
                    "model_id": record.model_id,
                    "model_version": record.model_version,
                    "vlm_backend": enum_to_str(record.vlm_backend),
                    "status": status,
                    "sample_count": record.sample_count,
                    "labeled_sample_count": record.labeled_sample_count,
                    "auto_label_manifest_uri": record.auto_label_manifest_uri,
                    "samples_root_uri": record.samples_root_uri,
                    "metrics": metrics,
                    "class_metrics": class_metrics,
                    "pipeline_run_id": record.pipeline_run_id,
                    "pipeline_step_run_id": record.pipeline_step_run_id,
                    "job_id": record.job_id,
                    "metadata": metadata,
                    "error": error,
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                },
            )
            .returning(AutoLabelRunModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(self, auto_label_run_id: str) -> AutoLabelRunRecord:
        model = await self.session.get(AutoLabelRunModel, auto_label_run_id)

        if model is None:
            raise FileNotFoundError(f"Auto-label run not found: {auto_label_run_id}")

        return self._to_schema(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
    ) -> list[AutoLabelRunRecord]:
        stmt = select(AutoLabelRunModel).order_by(AutoLabelRunModel.created_at.desc())

        if dataset_id is not None:
            stmt = stmt.where(AutoLabelRunModel.dataset_id == dataset_id)

        if dataset_version is not None:
            stmt = stmt.where(AutoLabelRunModel.dataset_version == dataset_version)

        if model_id is not None:
            stmt = stmt.where(AutoLabelRunModel.model_id == model_id)

        if model_version is not None:
            stmt = stmt.where(AutoLabelRunModel.model_version == model_version)

        if status is not None:
            stmt = stmt.where(AutoLabelRunModel.status == enum_to_str(status))

        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    def _to_schema(self, model: AutoLabelRunModel) -> AutoLabelRunRecord:
        return AutoLabelRunRecord.model_validate(
            {
                "id": model.id,
                "dataset_id": model.dataset_id,
                "dataset_version": model.dataset_version,
                "model_id": model.model_id,
                "model_version": model.model_version,
                "vlm_backend": model.vlm_backend,
                "status": model.status,
                "sample_count": model.sample_count,
                "labeled_sample_count": model.labeled_sample_count,
                "auto_label_manifest_uri": model.auto_label_manifest_uri,
                "samples_root_uri": model.samples_root_uri,
                "metrics": model.metrics or {},
                "class_metrics": model.class_metrics or {},
                "pipeline_run_id": model.pipeline_run_id,
                "pipeline_step_run_id": model.pipeline_step_run_id,
                "job_id": model.job_id,
                "metadata": model.metadata_ or {},
                "error": model.error,
                "created_at": model.created_at,
                "updated_at": model.updated_at,
                "started_at": model.started_at,
                "finished_at": model.finished_at,
            }
        )
