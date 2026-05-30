from __future__ import annotations

from sceneops_core.schemas.datasets import DatasetVersionStatus
from sceneops_core.schemas.jobs import (
    JobManifest,
    JobType,
    ValidateDatasetManifestJobParams,
    ValidateDatasetManifestJobResult,
)
from sceneops_worker.datasets.validation import validate_dataset_manifest
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class ValidateDatasetManifestJobHandler(
    TypedJobHandler[
        ValidateDatasetManifestJobParams,
        ValidateDatasetManifestJobResult,
    ]
):
    job_type = JobType.VALIDATE_DATASET_MANIFEST

    def parse_params(self, job: JobManifest) -> ValidateDatasetManifestJobParams:
        return ValidateDatasetManifestJobParams.model_validate(job.params)

    async def run(
        self,
        *,
        params: ValidateDatasetManifestJobParams,
        job: JobManifest,
    ) -> ValidateDatasetManifestJobResult:
        registry = self.context.dataset_registry_store

        version = await registry.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        if version.status not in {
            DatasetVersionStatus.INGESTED,
            DatasetVersionStatus.READY,
        }:
            raise ValueError(
                f"Dataset version is not validatable: "
                f"{params.dataset_id}:{params.dataset_version}, "
                f"status={version.status}"
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
            metadata={
                **(version.metadata or {}),
                "last_validate_job_id": job.job_id,
            },
        )

        try:
            dataset_manifest = (
                await self.context.dataset_artifact_store.load_dataset_manifest(
                    version.manifest_uri
                )
            )

            report = await validate_dataset_manifest(
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=self.context.dataset_artifact_store,
                require_target_channels=params.require_target_channels,
                validate_samples=params.validate_samples,
                max_samples=params.max_samples,
            )

            if not report.is_valid:
                raise ValueError(
                    "Dataset manifest validation failed: "
                    f"missing_scenes={len(report.missing_scene_ids)}, "
                    f"missing_samples={len(report.missing_sample_ids)}, "
                    f"missing_channels={len(report.missing_channels)}"
                )

            await registry.upsert_version(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=version.dataset_type,
                source_uri=version.source_uri,
                manifest_uri=version.manifest_uri,
                scene_count=dataset_manifest.summary.scene_count,
                sample_count=dataset_manifest.summary.sample_count,
                annotation_count=dataset_manifest.summary.annotation_count,
                status=DatasetVersionStatus.READY,
                metadata={
                    **(version.metadata or {}),
                    "last_validate_job_id": job.job_id,
                    "validated_scene_count": report.validated_scene_count,
                    "validated_sample_count": report.validated_sample_count,
                },
            )

            return ValidateDatasetManifestJobResult(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_manifest_uri=version.manifest_uri,
                scene_count=dataset_manifest.summary.scene_count,
                sample_count=dataset_manifest.summary.sample_count,
                annotation_count=dataset_manifest.summary.annotation_count,
                validated_scene_count=report.validated_scene_count,
                validated_sample_count=report.validated_sample_count,
                missing_sample_count=report.missing_sample_count,
                status="ready",
                result_summary={
                    "missing_scene_ids": report.missing_scene_ids,
                    "missing_sample_ids": report.missing_sample_ids,
                    "missing_channels": report.missing_channels,
                },
            )

        except Exception:
            await registry.upsert_version(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=version.dataset_type,
                source_uri=version.source_uri,
                manifest_uri=version.manifest_uri,
                scene_count=version.scene_count,
                sample_count=version.sample_count,
                annotation_count=version.annotation_count,
                status=DatasetVersionStatus.FAILED,
                metadata={
                    **(version.metadata or {}),
                    "last_failed_validate_job_id": job.job_id,
                },
            )
            raise
