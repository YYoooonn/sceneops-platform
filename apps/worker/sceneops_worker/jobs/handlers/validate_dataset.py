from __future__ import annotations

from datetime import UTC, datetime

from sceneops_core.ids.runs import default_validation_run_id
from sceneops_core.schemas.datasets import DatasetVersionStatus
from sceneops_core.schemas.datasets.validation import DatasetValidationStatus
from sceneops_core.schemas.jobs import (
    JobManifest,
    JobType,
    ValidateDatasetJobParams,
    ValidateDatasetJobResult,
)
from sceneops_core.schemas.runs import DatasetValidationRunRecord, RunStatus
from sceneops_worker.datasets.validation import validate_dataset
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class ValidateDatasetJobHandler(
    TypedJobHandler[
        ValidateDatasetJobParams,
        ValidateDatasetJobResult,
    ]
):
    job_type = JobType.VALIDATE_DATASET

    def parse_params(self, job: JobManifest) -> ValidateDatasetJobParams:
        return ValidateDatasetJobParams.model_validate(job.params)

    async def run(
        self,
        *,
        params: ValidateDatasetJobParams,
        job: JobManifest,
    ) -> ValidateDatasetJobResult:
        registry = self.context.dataset_registry_store
        run_registry = self.context.run_registry_store
        run_artifacts = self.context.run_artifact_store

        validation_run_id = default_validation_run_id(job.job_id)
        now = datetime.now(UTC)

        version = await registry.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )
        previous_status = version.status

        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        if version.status not in {
            DatasetVersionStatus.INGESTED,
            DatasetVersionStatus.READY,
            DatasetVersionStatus.FAILED,
        }:
            raise ValueError(
                f"Dataset version is not validatable: "
                f"{params.dataset_id}:{params.dataset_version}, "
                f"status={version.status}"
            )

        base_metadata = {
            **(version.metadata or {}),
            "last_validate_job_id": job.job_id,
            "last_validation_run_id": validation_run_id,
            "validation_status": "running",
            "validation_required_channels": params.require_target_channels,
            "validation_max_samples": params.max_samples,
        }

        await run_registry.upsert_validation_run(
            DatasetValidationRunRecord(
                id=validation_run_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                status=RunStatus.RUNNING,
                validation_status=None,
                dataset_manifest_uri=version.manifest_uri,
                job_id=job.job_id,
                metadata={
                    "required_channels": params.require_target_channels,
                    "max_samples": params.max_samples,
                    "validate_samples": params.validate_samples,
                    "validate_sensor_artifacts": params.validate_sensor_artifacts,
                    "validate_annotations": params.validate_annotations,
                    "validate_calibration": params.validate_calibration,
                },
                started_at=now,
            )
        )

        await registry.upsert_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            dataset_type=version.dataset_type,
            source_uri=version.source_uri,
            manifest_uri=version.manifest_uri,
            scene_count=version.scene_count,
            sample_count=version.sample_count,
            annotation_count=version.annotation_count,
            status=DatasetVersionStatus.VALIDATING,
            metadata=base_metadata,
        )

        try:
            dataset_manifest = (
                await self.context.dataset_artifact_store.load_dataset_manifest(
                    version.manifest_uri
                )
            )

            report = await validate_dataset(
                validation_run_id=validation_run_id,
                job_id=job.job_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_manifest_uri=version.manifest_uri,
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=self.context.dataset_artifact_store,
                require_target_channels=params.require_target_channels,
                validate_samples=params.validate_samples,
                max_samples=params.max_samples,
            )

            validation_report_uri = await run_artifacts.write_validation_run_manifest(
                validation_run_id=validation_run_id,
                manifest=report.model_dump(mode="json"),
            )

            finished_at = datetime.now(UTC)

            await run_registry.upsert_validation_run(
                DatasetValidationRunRecord(
                    id=validation_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    status=RunStatus.SUCCEEDED,
                    validation_status=report.status,
                    should_block_pipeline=report.should_block_pipeline,
                    dataset_manifest_uri=version.manifest_uri,
                    validation_report_uri=validation_report_uri,
                    scope=report.scope,
                    max_samples=params.max_samples,
                    scene_count=report.summary.scene_count,
                    sample_count=report.summary.sample_count,
                    annotation_count=report.summary.annotation_count,
                    validated_scene_count=report.summary.validated_scene_count,
                    validated_sample_count=report.summary.validated_sample_count,
                    issue_count=report.summary.issue_count,
                    error_count=report.summary.error_count,
                    warning_count=report.summary.warning_count,
                    missing_scene_count=report.summary.missing_scene_count,
                    missing_sample_count=report.summary.missing_sample_count,
                    missing_channel_count=report.summary.missing_channel_count,
                    missing_artifact_count=report.summary.missing_artifact_count,
                    job_id=job.job_id,
                    metadata=report.metadata,
                    started_at=now,
                    finished_at=finished_at,
                )
            )

            await registry.update_validation(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                validation_run_id=validation_run_id,
                validation_report_uri=validation_report_uri,
                report=report,
            )

            return ValidateDatasetJobResult(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_manifest_uri=version.manifest_uri,
                validation_run_id=validation_run_id,
                validation_report_uri=validation_report_uri,
                status=report.status,
                validation_scope=report.scope,
                should_block_pipeline=report.should_block_pipeline,
                scene_count=report.summary.scene_count,
                sample_count=report.summary.sample_count,
                annotation_count=report.summary.annotation_count,
                validated_scene_count=report.summary.validated_scene_count,
                validated_sample_count=report.summary.validated_sample_count,
                issue_count=report.summary.issue_count,
                error_count=report.summary.error_count,
                warning_count=report.summary.warning_count,
                missing_scene_count=report.summary.missing_scene_count,
                missing_sample_count=report.summary.missing_sample_count,
                missing_channel_count=report.summary.missing_channel_count,
                missing_artifact_count=report.summary.missing_artifact_count,
                result_summary={
                    "validation_run_id": validation_run_id,
                    "validation_report_uri": validation_report_uri,
                    "validation_status": report.status.value,
                    "should_block_pipeline": report.should_block_pipeline,
                    "issue_count": report.summary.issue_count,
                    "error_count": report.summary.error_count,
                    "warning_count": report.summary.warning_count,
                },
            )

        except Exception as exc:
            finished_at = datetime.now(UTC)
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
            }

            await run_registry.upsert_validation_run(
                DatasetValidationRunRecord(
                    id=validation_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    status=RunStatus.FAILED,
                    validation_status=DatasetValidationStatus.ERROR,
                    should_block_pipeline=True,
                    dataset_manifest_uri=version.manifest_uri,
                    job_id=job.job_id,
                    metadata={
                        "required_channels": params.require_target_channels,
                        "max_samples": params.max_samples,
                    },
                    error=error,
                    started_at=now,
                    finished_at=finished_at,
                )
            )

            await registry.upsert_version(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=version.dataset_type,
                source_uri=version.source_uri,
                manifest_uri=version.manifest_uri,
                scene_count=version.scene_count,
                sample_count=version.sample_count,
                annotation_count=version.annotation_count,
                status=previous_status,
                metadata={
                    **base_metadata,
                    "validation_status": "error",
                    "last_failed_validation_run_id": validation_run_id,
                    "validation_error_type": type(exc).__name__,
                    "validation_error_message": str(exc),
                },
            )
            raise
