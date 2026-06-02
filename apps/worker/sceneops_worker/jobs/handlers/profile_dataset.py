from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.common.ids import default_profile_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetProfileScope
from sceneops_core.jobs.schemas import (
    JobType,
    ProfileDatasetJobParams,
    ProfileDatasetJobResult,
)
from sceneops_core.runs.schemas import DatasetProfileRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.datasets.profiling import (
    DatasetProfileRequest,
    create_dataset_profiler,
)
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.jobs.context import JobContext
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


class ProfileDatasetJobHandler(
    RunRecordHandler[
        ProfileDatasetJobParams, ProfileDatasetJobResult, DatasetProfileRunRecord
    ],
    JobHandler[ProfileDatasetJobParams, ProfileDatasetJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.PROFILE_DATASET

    @property
    def params_model(self) -> type[ProfileDatasetJobParams]:
        return ProfileDatasetJobParams

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        return {
            **base,
            "dataset_manifest_uri": (
                base.get("dataset_manifest_uri")
                or context_values.get(Ctx.DATASET_MANIFEST_URI)
            ),
            "require_target_channels": base.get(
                "require_target_channels", ["CAM_FRONT", "LIDAR_TOP"]
            ),
            "profile_samples": base.get("profile_samples", True),
            "profile_annotations": base.get("profile_annotations", True),
            "profile_sensor_coverage": base.get("profile_sensor_coverage", True),
            "profile_scene_distribution": base.get("profile_scene_distribution", True),
        }

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = ProfileDatasetJobResult.model_validate(result)
        return {
            Ctx.DATASET_ID: parsed.dataset_id,
            Ctx.DATASET_VERSION: parsed.dataset_version,
            Ctx.DATASET_MANIFEST_URI: parsed.dataset_manifest_uri,
            Ctx.PROFILE_RUN_ID: parsed.profile_run_id,
            Ctx.PROFILE_REPORT_URI: parsed.profile_report_uri,
            Ctx.SCENE_COUNT: parsed.scene_count,
            Ctx.SAMPLE_COUNT: parsed.sample_count,
            Ctx.ANNOTATION_COUNT: parsed.annotation_count,
            Ctx.PROFILE_SCENE_COUNT: parsed.profiled_scene_count,
            Ctx.PROFILE_SAMPLE_COUNT: parsed.profiled_sample_count,
            Ctx.OBSERVED_CHANNELS: parsed.observed_channels,
            Ctx.OBSERVED_CHANNEL_COUNT: len(parsed.observed_channels),
            Ctx.MISSING_REQUIRED_CHANNEL_COUNT: parsed.missing_required_channel_count,
            Ctx.SENSOR_COVERAGE_RATIO: parsed.sensor_coverage_ratio,
            Ctx.EMPTY_ANNOTATION_SAMPLE_COUNT: parsed.empty_annotation_sample_count,
            Ctx.EMPTY_ANNOTATION_SAMPLE_RATIO: parsed.empty_annotation_sample_ratio,
            Ctx.PROFILE_SUMMARY: parsed.result_summary,
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: ProfileDatasetJobParams,
        started_at: datetime,
    ) -> DatasetProfileRunRecord:
        scope = (
            DatasetProfileScope.SAMPLED
            if params.max_samples is not None
            else DatasetProfileScope.FULL
        )
        return DatasetProfileRunRecord(
            id=default_profile_run_id(job.job_id),
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            status=RunStatus.RUNNING,
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
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: ProfileDatasetJobParams,
        context: JobContext,
        initial_record: DatasetProfileRunRecord,
        started_at: datetime,
    ) -> tuple[DatasetProfileRunRecord, ProfileDatasetJobResult]:
        profile_run_id = initial_record.id

        version = await context.dataset_registry_store.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        # patch manifest_uri into the initial record now that we have it
        await context.run_registry_store.upsert_profile_run(
            initial_record.model_copy(
                update={"dataset_manifest_uri": version.manifest_uri}
            )
        )

        dataset_manifest = await context.dataset_artifact_store.load_dataset_manifest(
            version.manifest_uri
        )

        profiler = create_dataset_profiler()

        report = await profiler.run(
            DatasetProfileRequest(
                profile_run_id=profile_run_id,
                job_id=job.job_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_manifest_uri=version.manifest_uri,
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=context.dataset_artifact_store,
                required_channels=params.require_target_channels,
                scope=initial_record.scope,
                max_samples=params.max_samples,
                profile_samples=params.profile_samples,
                profile_annotations=params.profile_annotations,
                profile_sensor_coverage=params.profile_sensor_coverage,
                profile_scene_distribution=params.profile_scene_distribution,
            )
        )

        profile_report_uri = (
            await context.run_artifact_store.write_dataset_profile_run_report(
                profile_run_id=profile_run_id,
                manifest=report.model_dump(mode="json"),
            )
        )

        await context.dataset_registry_store.update_profile(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            profile_run_id=profile_run_id,
            profile_report_uri=profile_report_uri,
            report=report,
        )

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "dataset_manifest_uri": version.manifest_uri,
                "profile_report_uri": profile_report_uri,
                "scene_count": report.summary.scene_count,
                "sample_count": report.summary.sample_count,
                "annotation_count": report.summary.annotation_count,
                "profiled_scene_count": report.summary.profiled_scene_count,
                "profiled_sample_count": report.summary.profiled_sample_count,
                "observed_channel_count": report.summary.observed_channel_count,
                "missing_required_channel_count": report.summary.missing_required_channel_count,
                "sensor_coverage_ratio": report.summary.sensor_coverage_ratio,
                "empty_annotation_sample_count": report.summary.empty_annotation_sample_count,
                "empty_annotation_sample_ratio": report.summary.empty_annotation_sample_ratio,
                "metadata": {
                    **(report.metadata or {}),
                    "profiler_id": profiler.profiler_id,
                },
                "finished_at": utc_now(),
            }
        )

        job_result = ProfileDatasetJobResult(
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

        return succeeded_record, job_result

    async def _upsert(
        self, context: JobContext, record: DatasetProfileRunRecord
    ) -> None:
        await context.run_registry_store.upsert_profile_run(record)
