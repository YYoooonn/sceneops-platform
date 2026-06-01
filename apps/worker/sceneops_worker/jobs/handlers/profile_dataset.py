from __future__ import annotations

from datetime import UTC, datetime

from sceneops_core.common.ids import default_profile_run_id
from sceneops_core.datasets.schemas import DatasetProfileScope
from sceneops_core.jobs.schemas import (
    JobManifest,
    JobType,
    ProfileDatasetJobParams,
    ProfileDatasetJobResult,
)
from sceneops_core.runs.schemas import DatasetProfileRunRecord, RunStatus
from sceneops_worker.datasets.profiling import profile_dataset
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class ProfileDatasetJobHandler(
    TypedJobHandler[ProfileDatasetJobParams, ProfileDatasetJobResult]
):
    job_type = JobType.PROFILE_DATASET

    def parse_params(self, job: JobManifest) -> ProfileDatasetJobParams:
        return ProfileDatasetJobParams.model_validate(job.params)

    async def run(
        self,
        *,
        params: ProfileDatasetJobParams,
        job: JobManifest,
    ) -> ProfileDatasetJobResult:
        now = datetime.now(UTC)
        profile_run_id = default_profile_run_id(job.job_id)

        version = await self.context.dataset_registry_store.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        scope = (
            DatasetProfileScope.SAMPLED
            if params.max_samples is not None
            else DatasetProfileScope.FULL
        )

        await self.context.run_registry_store.upsert_profile_run(
            DatasetProfileRunRecord(
                id=profile_run_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                status=RunStatus.RUNNING,
                dataset_manifest_uri=version.manifest_uri,
                scope=scope,
                max_samples=params.max_samples,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_step_run_id=job.pipeline_step_run_id,
                job_id=job.job_id,
                metadata={
                    "required_channels": params.require_target_channels,
                    "profile_samples": params.profile_samples,
                    "profile_annotations": params.profile_annotations,
                    "profile_sensor_coverage": params.profile_sensor_coverage,
                    "profile_scene_distribution": params.profile_scene_distribution,
                },
                started_at=now,
            )
        )

        try:
            dataset_manifest = (
                await self.context.dataset_artifact_store.load_dataset_manifest(
                    version.manifest_uri
                )
            )

            report = await profile_dataset(
                profile_run_id=profile_run_id,
                job_id=job.job_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_manifest_uri=version.manifest_uri,
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=self.context.dataset_artifact_store,
                required_channels=params.require_target_channels,
                scope=scope,
                max_samples=params.max_samples,
                profile_samples=params.profile_samples,
                profile_annotations=params.profile_annotations,
                profile_sensor_coverage=params.profile_sensor_coverage,
                profile_scene_distribution=params.profile_scene_distribution,
            )

            profile_report_uri = (
                await self.context.run_artifact_store.write_dataset_profile_run_report(
                    profile_run_id=profile_run_id,
                    manifest=report.model_dump(mode="json"),
                )
            )

            finished_at = datetime.now(UTC)

            await self.context.run_registry_store.upsert_profile_run(
                DatasetProfileRunRecord(
                    id=profile_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    status=RunStatus.SUCCEEDED,
                    dataset_manifest_uri=version.manifest_uri,
                    profile_report_uri=profile_report_uri,
                    scope=scope,
                    max_samples=params.max_samples,
                    scene_count=report.summary.scene_count,
                    sample_count=report.summary.sample_count,
                    annotation_count=report.summary.annotation_count,
                    profiled_scene_count=report.summary.profiled_scene_count,
                    profiled_sample_count=report.summary.profiled_sample_count,
                    observed_channel_count=report.summary.observed_channel_count,
                    missing_required_channel_count=report.summary.missing_required_channel_count,
                    sensor_coverage_ratio=report.summary.sensor_coverage_ratio,
                    empty_annotation_sample_count=report.summary.empty_annotation_sample_count,
                    empty_annotation_sample_ratio=report.summary.empty_annotation_sample_ratio,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    metadata=report.metadata,
                    started_at=now,
                    finished_at=finished_at,
                )
            )

            await self.context.dataset_registry_store.update_profile(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                profile_run_id=profile_run_id,
                profile_report_uri=profile_report_uri,
                report=report,
            )

            return ProfileDatasetJobResult(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_manifest_uri=version.manifest_uri,
                profile_run_id=profile_run_id,
                profile_report_uri=profile_report_uri,
                scene_count=report.summary.scene_count,
                sample_count=report.summary.sample_count,
                annotation_count=report.summary.annotation_count,
                profiled_scene_count=report.summary.profiled_scene_count,
                profiled_sample_count=report.summary.profiled_sample_count,
                observed_channels=report.observed_channels,
                missing_required_channel_count=report.summary.missing_required_channel_count,
                sensor_coverage_ratio=report.summary.sensor_coverage_ratio,
                empty_annotation_sample_count=report.summary.empty_annotation_sample_count,
                empty_annotation_sample_ratio=report.summary.empty_annotation_sample_ratio,
                result_summary={
                    "profile_run_id": profile_run_id,
                    "profile_report_uri": profile_report_uri,
                    "observed_channel_count": report.summary.observed_channel_count,
                    "sensor_coverage_ratio": report.summary.sensor_coverage_ratio,
                    "empty_annotation_sample_ratio": report.summary.empty_annotation_sample_ratio,
                },
            )

        except Exception as exc:
            finished_at = datetime.now(UTC)

            await self.context.run_registry_store.upsert_profile_run(
                DatasetProfileRunRecord(
                    id=profile_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    status=RunStatus.FAILED,
                    dataset_manifest_uri=version.manifest_uri,
                    scope=scope,
                    max_samples=params.max_samples,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                    started_at=now,
                    finished_at=finished_at,
                )
            )
            raise
