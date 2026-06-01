from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import (
    DatasetValidationStatus,
    DatasetVersionRecord,
    DatasetVersionStatus,
)
from sceneops_db.datasets.models import DatasetVersionModel
from sceneops_db.utils import enum_to_str


def make_dataset_version_id(dataset_id: str, version: str) -> str:
    return f"{dataset_id}:{version}"


class PostgresDatasetVersionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, record: DatasetVersionRecord) -> DatasetVersionRecord:
        model = self._to_model(record)

        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord:
        stmt = select(DatasetVersionModel).where(
            DatasetVersionModel.dataset_id == dataset_id,
            DatasetVersionModel.version == version,
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()

        if model is None:
            raise FileNotFoundError(
                f"Dataset version not found: {dataset_id}:{version}"
            )

        return self._to_schema(model)

    async def list(
        self,
        *,
        dataset_id: str,
    ) -> list[DatasetVersionRecord]:
        stmt = (
            select(DatasetVersionModel)
            .where(DatasetVersionModel.dataset_id == dataset_id)
            .order_by(DatasetVersionModel.created_at.desc())
        )

        result = await self.session.execute(stmt)
        models = result.scalars().all()

        return [self._to_schema(model) for model in models]

    async def update(
        self,
        record: DatasetVersionRecord,
    ) -> DatasetVersionRecord:
        model = await self.session.get(DatasetVersionModel, record.id)

        if model is None:
            raise FileNotFoundError(f"Dataset version not found: {record.id}")

        updated = self._to_model(record)

        model.dataset_id = updated.dataset_id
        model.version = updated.version
        model.dataset_type = updated.dataset_type
        model.manifest_uri = updated.manifest_uri
        model.source_uri = updated.source_uri
        model.scene_count = updated.scene_count
        model.sample_count = updated.sample_count
        model.annotation_count = updated.annotation_count
        model.status = updated.status
        model.metadata_ = updated.metadata_
        model.latest_validation_run_id = updated.latest_validation_run_id
        model.validation_status = updated.validation_status
        model.should_block_pipeline = updated.should_block_pipeline
        model.validation_report_uri = updated.validation_report_uri
        model.validation_issue_count = updated.validation_issue_count
        model.validation_error_count = updated.validation_error_count
        model.validation_warning_count = updated.validation_warning_count
        model.missing_scene_count = updated.missing_scene_count
        model.missing_sample_count = updated.missing_sample_count
        model.missing_channel_count = updated.missing_channel_count
        model.missing_artifact_count = updated.missing_artifact_count
        model.latest_profile_run_id = updated.latest_profile_run_id
        model.profile_report_uri = updated.profile_report_uri
        model.profiled_scene_count = updated.profiled_scene_count
        model.profiled_sample_count = updated.profiled_sample_count
        model.observed_channel_count = updated.observed_channel_count
        model.observed_channels = updated.observed_channels or []
        model.missing_required_channel_count = updated.missing_required_channel_count
        model.sensor_coverage_ratio = updated.sensor_coverage_ratio
        model.empty_annotation_sample_count = updated.empty_annotation_sample_count
        model.empty_annotation_sample_ratio = updated.empty_annotation_sample_ratio

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def upsert(
        self,
        *,
        dataset_id: str,
        version: str,
        dataset_type: str,
        manifest_uri: str | None = None,
        source_uri: str | None = None,
        scene_count: int | None = None,
        sample_count: int | None = None,
        annotation_count: int | None = None,
        status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED,
        metadata: JsonDict | None = None,
    ) -> DatasetVersionRecord:
        version_id = make_dataset_version_id(dataset_id, version)
        dataset_type_value = enum_to_str(dataset_type)
        status_value = enum_to_str(status)

        stmt = (
            insert(DatasetVersionModel)
            .values(
                id=version_id,
                dataset_id=dataset_id,
                version=version,
                dataset_type=dataset_type_value,
                manifest_uri=manifest_uri,
                source_uri=source_uri,
                scene_count=scene_count,
                sample_count=sample_count,
                annotation_count=annotation_count,
                status=status_value,
                metadata_=metadata or {},
            )
            .on_conflict_do_update(
                constraint="uq_dataset_versions_dataset_id_version",
                set_={
                    "dataset_type": dataset_type_value,
                    "manifest_uri": manifest_uri,
                    "source_uri": source_uri,
                    "scene_count": scene_count,
                    "sample_count": sample_count,
                    "annotation_count": annotation_count,
                    "status": status_value,
                    "metadata": metadata or {},
                },
            )
            .returning(DatasetVersionModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one()

        await self.session.commit()
        await self.session.refresh(model)

        return self._to_schema(model)

    async def update_latest_profile_summary(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        profile_run_id: str,
        profile_report_uri: str,
        profiled_scene_count: int,
        profiled_sample_count: int,
        observed_channel_count: int,
        observed_channels: list[str],
        missing_required_channel_count: int,
        sensor_coverage_ratio: float,
        empty_annotation_sample_count: int,
        empty_annotation_sample_ratio: float,
    ) -> DatasetVersionRecord:
        version_id = f"{dataset_id}:{dataset_version}"

        stmt = (
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == version_id)
            .values(
                latest_profile_run_id=profile_run_id,
                profile_report_uri=profile_report_uri,
                profiled_scene_count=profiled_scene_count,
                profiled_sample_count=profiled_sample_count,
                observed_channel_count=observed_channel_count,
                observed_channels=observed_channels,
                missing_required_channel_count=missing_required_channel_count,
                sensor_coverage_ratio=sensor_coverage_ratio,
                empty_annotation_sample_count=empty_annotation_sample_count,
                empty_annotation_sample_ratio=empty_annotation_sample_ratio,
            )
            .returning(DatasetVersionModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise FileNotFoundError(
                f"Dataset version not found: {dataset_id}:{dataset_version}"
            )

        await self.session.commit()
        await self.session.refresh(model)
        return self._to_schema(model)

    async def update_latest_validation_summary(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        dataset_status: str | DatasetVersionStatus,
        validation_run_id: str,
        validation_status: str | DatasetValidationStatus,
        should_block_pipeline: bool,
        validation_report_uri: str,
        issue_count: int,
        error_count: int,
        warning_count: int,
        missing_scene_count: int,
        missing_sample_count: int,
        missing_channel_count: int,
        missing_artifact_count: int,
    ) -> DatasetVersionRecord:
        version_id = f"{dataset_id}:{dataset_version}"

        stmt = (
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == version_id)
            .values(
                latest_validation_run_id=validation_run_id,
                status=enum_to_str(dataset_status),
                validation_status=enum_to_str(validation_status),
                should_block_pipeline=should_block_pipeline,
                validation_report_uri=validation_report_uri,
                validation_issue_count=issue_count,
                validation_error_count=error_count,
                validation_warning_count=warning_count,
                missing_scene_count=missing_scene_count,
                missing_sample_count=missing_sample_count,
                missing_channel_count=missing_channel_count,
                missing_artifact_count=missing_artifact_count,
            )
            .returning(DatasetVersionModel)
        )

        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise FileNotFoundError(
                f"Dataset version not found: {dataset_id}:{dataset_version}"
            )

        await self.session.commit()
        await self.session.refresh(model)
        return self._to_schema(model)

    def _to_model(self, record: DatasetVersionRecord) -> DatasetVersionModel:
        return DatasetVersionModel(
            id=record.id,
            dataset_id=record.dataset_id,
            version=record.version,
            dataset_type=enum_to_str(record.dataset_type),
            manifest_uri=record.manifest_uri,
            source_uri=record.source_uri,
            scene_count=record.scene_count,
            sample_count=record.sample_count,
            annotation_count=record.annotation_count,
            status=enum_to_str(record.status),
            latest_validation_run_id=record.latest_validation_run_id,
            validation_status=record.validation_status,
            should_block_pipeline=record.should_block_pipeline,
            validation_report_uri=record.validation_report_uri,
            validation_issue_count=record.validation_issue_count,
            validation_error_count=record.validation_error_count,
            validation_warning_count=record.validation_warning_count,
            missing_scene_count=record.missing_scene_count,
            missing_sample_count=record.missing_sample_count,
            missing_channel_count=record.missing_channel_count,
            missing_artifact_count=record.missing_artifact_count,
            latest_profile_run_id=record.latest_profile_run_id,
            profile_report_uri=record.profile_report_uri,
            profiled_scene_count=record.profiled_scene_count,
            profiled_sample_count=record.profiled_sample_count,
            observed_channel_count=record.observed_channel_count,
            observed_channels=record.observed_channels or [],
            missing_required_channel_count=record.missing_required_channel_count,
            sensor_coverage_ratio=record.sensor_coverage_ratio,
            empty_annotation_sample_count=record.empty_annotation_sample_count,
            empty_annotation_sample_ratio=record.empty_annotation_sample_ratio,
            metadata_=record.metadata,
        )

    def _to_schema(self, model: DatasetVersionModel) -> DatasetVersionRecord:
        return DatasetVersionRecord.model_validate({
            "id":model.id,
            "dataset_id": model.dataset_id,
            "version": model.version,
            "dataset_type": model.dataset_type,
            "manifest_uri": model.manifest_uri,
            "source_uri": model.source_uri,
            "scene_count": model.scene_count,
            "sample_count": model.sample_count,
            "annotation_count": model.annotation_count,
            "status": model.status,
            "latest_validation_run_id": model.latest_validation_run_id,
            "validation_status": model.validation_status,
            "should_block_pipeline": model.should_block_pipeline,
            "validation_report_uri": model.validation_report_uri,
            "validation_issue_count": model.validation_issue_count,
            "validation_error_count": model.validation_error_count,
            "validation_warning_count": model.validation_warning_count,
            "missing_scene_count": model.missing_scene_count,
            "missing_sample_count": model.missing_sample_count,
            "missing_channel_count": model.missing_channel_count,
            "missing_artifact_count": model.missing_artifact_count,

            "latest_profile_run_id": model.latest_profile_run_id,
            "profile_report_uri": model.profile_report_uri,
            "profiled_scene_count": model.profiled_scene_count,
            "profiled_sample_count": model.profiled_sample_count,
            "observed_channel_count": model.observed_channel_count,
            "observed_channels": model.observed_channels or [],
            "missing_required_channel_count": model.missing_required_channel_count,
            "sensor_coverage_ratio": model.sensor_coverage_ratio,
            "empty_annotation_sample_count": model.empty_annotation_sample_count,
            "empty_annotation_sample_ratio": model.empty_annotation_sample_ratio,
            "metadata": model.metadata_ or {},
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        })
