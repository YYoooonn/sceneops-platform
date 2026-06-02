from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import DatasetProfileRunRecord, RunStatus
from sceneops_db.runs.models import DatasetProfileRunModel
from sceneops_db.utils import enum_to_str, to_error_info, to_error_json, to_jsonable


class PostgresDatasetProfileRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        record: DatasetProfileRunRecord,
    ) -> DatasetProfileRunRecord:
        values = {
            "id": record.id,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "status": enum_to_str(record.status),
            "dataset_manifest_uri": record.dataset_manifest_uri,
            "profile_report_uri": record.profile_report_uri,
            "scope": enum_to_str(record.scope),
            "max_samples": record.max_samples,
            "scene_count": record.scene_count,
            "sample_count": record.sample_count,
            "annotation_count": record.annotation_count,
            "profiled_scene_count": record.profiled_scene_count,
            "profiled_sample_count": record.profiled_sample_count,
            "observed_channel_count": record.observed_channel_count,
            "missing_required_channel_count": record.missing_required_channel_count,
            "sensor_coverage_ratio": record.sensor_coverage_ratio,
            "empty_annotation_sample_count": record.empty_annotation_sample_count,
            "empty_annotation_sample_ratio": record.empty_annotation_sample_ratio,
            "pipeline_run_id": record.pipeline_run_id,
            "pipeline_step_run_id": record.pipeline_step_run_id,
            "job_id": record.job_id,
            "metadata_": to_jsonable(record.metadata) or {},
            "error": to_error_json(record.error),
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }

        update_values = {key: value for key, value in values.items() if key != "id"}
        update_values["metadata"] = update_values.pop("metadata_")

        stmt = (
            insert(DatasetProfileRunModel)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[DatasetProfileRunModel.id],
                set_=update_values,
            )
            .returning(DatasetProfileRunModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()
        await self.session.commit()
        await self.session.refresh(model)
        return self._to_schema(model)

    async def get(
        self,
        profile_run_id: str,
    ) -> DatasetProfileRunRecord:
        model = await self.session.get(DatasetProfileRunModel, profile_run_id)
        if model is None:
            raise FileNotFoundError(f"Dataset profile run not found: {profile_run_id}")
        return self._to_schema(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: RunStatus | None = None,
    ) -> list[DatasetProfileRunRecord]:
        stmt = select(DatasetProfileRunModel).order_by(
            DatasetProfileRunModel.created_at.desc()
        )

        if dataset_id is not None:
            stmt = stmt.where(DatasetProfileRunModel.dataset_id == dataset_id)

        if dataset_version is not None:
            stmt = stmt.where(DatasetProfileRunModel.dataset_version == dataset_version)

        if status is not None:
            stmt = stmt.where(DatasetProfileRunModel.status == enum_to_str(status))

        result = await self.session.execute(stmt)
        return [self._to_schema(model) for model in result.scalars().all()]

    def _to_schema(
        self,
        model: DatasetProfileRunModel,
    ) -> DatasetProfileRunRecord:
        return DatasetProfileRunRecord.model_validate(
            {
                "id": model.id,
                "dataset_id": model.dataset_id,
                "dataset_version": model.dataset_version,
                "status": model.status,
                "dataset_manifest_uri": model.dataset_manifest_uri,
                "profile_report_uri": model.profile_report_uri,
                "scope": model.scope,
                "max_samples": model.max_samples,
                "scene_count": model.scene_count,
                "sample_count": model.sample_count,
                "annotation_count": model.annotation_count,
                "profiled_scene_count": model.profiled_scene_count,
                "profiled_sample_count": model.profiled_sample_count,
                "observed_channel_count": model.observed_channel_count,
                "missing_required_channel_count": model.missing_required_channel_count,
                "sensor_coverage_ratio": model.sensor_coverage_ratio,
                "empty_annotation_sample_count": model.empty_annotation_sample_count,
                "empty_annotation_sample_ratio": model.empty_annotation_sample_ratio,
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
