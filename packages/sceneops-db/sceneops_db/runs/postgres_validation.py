from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.schemas.datasets.validation import DatasetValidationStatus
from sceneops_core.schemas.runs import DatasetValidationRunRecord, RunStatus
from sceneops_db.runs.models import DatasetValidationRunModel
from sceneops_db.utils import enum_to_str, to_error_info, to_error_json, to_jsonable


class PostgresDatasetValidationRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        record: DatasetValidationRunRecord,
    ) -> DatasetValidationRunRecord:
        status = enum_to_str(record.status)
        validation_status = enum_to_str(record.validation_status)
        scope = enum_to_str(record.scope)
        metadata = to_jsonable(record.metadata) or {}
        error = to_error_json(record.error)

        values = {
            "id": record.id,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "status": status,
            "validation_status": validation_status,
            "should_block_pipeline": record.should_block_pipeline,
            "dataset_manifest_uri": record.dataset_manifest_uri,
            "validation_report_uri": record.validation_report_uri,
            "scope": scope,
            "max_samples": record.max_samples,
            "scene_count": record.scene_count,
            "sample_count": record.sample_count,
            "annotation_count": record.annotation_count,
            "validated_scene_count": record.validated_scene_count,
            "validated_sample_count": record.validated_sample_count,
            "issue_count": record.issue_count,
            "error_count": record.error_count,
            "warning_count": record.warning_count,
            "missing_scene_count": record.missing_scene_count,
            "missing_sample_count": record.missing_sample_count,
            "missing_channel_count": record.missing_channel_count,
            "missing_artifact_count": record.missing_artifact_count,
            "pipeline_run_id": record.pipeline_run_id,
            "pipeline_step_run_id": record.pipeline_step_run_id,
            "job_id": record.job_id,
            "metadata_": metadata,
            "error": error,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }

        update_values = {
            key: value
            for key, value in values.items()
            if key not in {"id", "created_at"}
        }
        update_values["metadata"] = update_values.pop("metadata_")

        stmt = (
            insert(DatasetValidationRunModel)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[DatasetValidationRunModel.id],
                set_=update_values,
            )
            .returning(DatasetValidationRunModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()
        await self.session.commit()
        await self.session.refresh(model)
        return self._to_schema(model)

    async def get(
        self,
        validation_run_id: str,
    ) -> DatasetValidationRunRecord:
        model = await self.session.get(
            DatasetValidationRunModel,
            validation_run_id,
        )
        if model is None:
            raise FileNotFoundError(
                f"Dataset validation run not found: {validation_run_id}"
            )
        return self._to_schema(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: RunStatus | None = None,
        validation_status: DatasetValidationStatus | None = None,
    ) -> list[DatasetValidationRunRecord]:
        stmt = select(DatasetValidationRunModel).order_by(
            DatasetValidationRunModel.created_at.desc()
        )

        if dataset_id is not None:
            stmt = stmt.where(DatasetValidationRunModel.dataset_id == dataset_id)

        if dataset_version is not None:
            stmt = stmt.where(
                DatasetValidationRunModel.dataset_version == dataset_version
            )

        if status is not None:
            stmt = stmt.where(DatasetValidationRunModel.status == enum_to_str(status))

        if validation_status is not None:
            stmt = stmt.where(
                DatasetValidationRunModel.validation_status
                == enum_to_str(validation_status)
            )

        result = await self.session.execute(stmt)
        return [self._to_schema(model) for model in result.scalars().all()]

    def _to_schema(
        self,
        model: DatasetValidationRunModel,
    ) -> DatasetValidationRunRecord:
        return DatasetValidationRunRecord.model_validate(
            {
                "id": model.id,
                "status": model.status,
                "dataset_id": model.dataset_id,
                "dataset_version": model.dataset_version,
                "validation_status": model.validation_status,
                "should_block_pipeline": model.should_block_pipeline,
                "dataset_manifest_uri": model.dataset_manifest_uri,
                "validation_report_uri": model.validation_report_uri,
                "scope": model.scope,
                "max_samples": model.max_samples,
                "scene_count": model.scene_count,
                "sample_count": model.sample_count,
                "annotation_count": model.annotation_count,
                "validated_scene_count": model.validated_scene_count,
                "validated_sample_count": model.validated_sample_count,
                "issue_count": model.issue_count,
                "error_count": model.error_count,
                "warning_count": model.warning_count,
                "missing_scene_count": model.missing_scene_count,
                "missing_sample_count": model.missing_sample_count,
                "missing_channel_count": model.missing_channel_count,
                "missing_artifact_count": model.missing_artifact_count,
                "pipeline_run_id": model.pipeline_run_id,
                "pipeline_step_run_id": model.pipeline_step_run_id,
                "job_id": model.job_id,
                "metadata": model.metadata_ or {},
                "error": to_error_info(model.error),
                "created_at": model.created_at,
                "updated_at": model.updated_at,
                "started_at": model.started_at,
                "finished_at": model.finished_at,
            }
        )
