from __future__ import annotations

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import (
    DatasetValidationReport,
    DatasetVersionRecord,
    DatasetVersionStatus,
)
from sceneops_core.schemas.datasets.profile import DatasetProfileReport
from sceneops_db.datasets import PostgresDatasetVersionRepository
from sceneops_db.session import async_session_scope


class DatasetRegistryStore:
    async def get_version(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetVersionRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetVersionRepository(session)
            return await repository.get(
                dataset_id=dataset_id,
                version=dataset_version,
            )

    async def upsert_version(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        dataset_type: str,
        manifest_uri: str | None = None,
        source_uri: str | None = None,
        scene_count: int | None = None,
        sample_count: int | None = None,
        annotation_count: int | None = None,
        status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED,
        metadata: JsonDict | None = None,
    ) -> DatasetVersionRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetVersionRepository(session)
            return await repository.upsert(
                dataset_id=dataset_id,
                version=dataset_version,
                dataset_type=dataset_type,
                manifest_uri=manifest_uri,
                source_uri=source_uri,
                scene_count=scene_count,
                sample_count=sample_count,
                annotation_count=annotation_count,
                status=status,
                metadata=metadata,
            )

    async def update_validation(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        validation_run_id: str,
        validation_report_uri: str,
        report: DatasetValidationReport,
    ) -> DatasetVersionRecord:
        next_dataset_status = (
            DatasetVersionStatus.FAILED
            if report.should_block_pipeline
            else DatasetVersionStatus.READY
        )
        async with async_session_scope() as session:
            repository = PostgresDatasetVersionRepository(session)
            return await repository.update_latest_validation_summary(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                dataset_status=next_dataset_status.value,
                validation_run_id=validation_run_id,
                validation_report_uri=validation_report_uri,
                validation_status=report.status.value,
                should_block_pipeline=report.should_block_pipeline,
                issue_count=report.summary.issue_count,
                error_count=report.summary.error_count,
                warning_count=report.summary.warning_count,
                missing_scene_count=report.summary.missing_scene_count,
                missing_sample_count=report.summary.missing_sample_count,
                missing_channel_count=report.summary.missing_channel_count,
                missing_artifact_count=report.summary.missing_artifact_count,
            )

    async def update_profile(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        profile_run_id: str,
        profile_report_uri: str,
        report: DatasetProfileReport,
    ) -> DatasetVersionRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetVersionRepository(session)
            return await repository.update_latest_profile_summary(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                profile_run_id=profile_run_id,
                profile_report_uri=profile_report_uri,
                profiled_scene_count=report.summary.profiled_scene_count,
                profiled_sample_count=report.summary.profiled_sample_count,
                observed_channel_count=report.summary.observed_channel_count,
                observed_channels=report.observed_channels,
                missing_required_channel_count=report.summary.missing_required_channel_count,
                sensor_coverage_ratio=report.summary.sensor_coverage_ratio,
                empty_annotation_sample_count=report.summary.empty_annotation_sample_count,
                empty_annotation_sample_ratio=report.summary.empty_annotation_sample_ratio,
            )
