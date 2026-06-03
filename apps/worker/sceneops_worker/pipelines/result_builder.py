from __future__ import annotations

from typing import Any

from sceneops_core.jobs.schemas import JobManifest
from sceneops_core.pipelines.schemas import (
    PipelineBuildScenesOutput,
    PipelineDatasetOutput,
    PipelineEvaluationOutput,
    PipelineInferenceOutput,
    PipelineResultLineage,
    PipelineResultOutputs,
    PipelineResultSummary,
    PipelineRunManifest,
    PipelineRunResult,
    PipelineRunStatus,
    PipelineStepResult,
    PipelineValidationOutput,
    PipelineStepRunManifest,
    PipelineProfileOutput,
)
from sceneops_worker.pipelines.context import PipelineExecutionContext
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


def build_pipeline_result(
    *,
    pipeline_run: PipelineRunManifest,
    context: PipelineExecutionContext,
    steps: list[PipelineStepResult],
    status: PipelineRunStatus,
) -> PipelineRunResult:
    return PipelineRunResult(
        summary=PipelineResultSummary(
            status=status,
            dataset_status=context.get(Ctx.DATASET_STATUS),
            validation_status=context.get(Ctx.VALIDATION_STATUS),
            should_block_pipeline=context.get(Ctx.SHOULD_BLOCK_PIPELINE),
            scene_count=context.get(Ctx.SCENE_COUNT),
            sample_count=context.get(Ctx.SAMPLE_COUNT),
            annotation_count=context.get(Ctx.ANNOTATION_COUNT),
            validated_scene_count=context.get(Ctx.VALIDATED_SCENE_COUNT),
            validated_sample_count=context.get(Ctx.VALIDATED_SAMPLE_COUNT),
            issue_count=context.get(Ctx.ISSUE_COUNT),
            error_count=context.get(Ctx.ERROR_COUNT),
            warning_count=context.get(Ctx.WARNING_COUNT),
            metrics=context.get(Ctx.EVALUATION_METRICS),
            profiled_scene_count=context.get(Ctx.PROFILE_SCENE_COUNT),
            profiled_sample_count=context.get(Ctx.PROFILE_SAMPLE_COUNT),
            observed_channel_count=context.get(Ctx.OBSERVED_CHANNEL_COUNT),
            sensor_coverage_ratio=context.get(Ctx.SENSOR_COVERAGE_RATIO),
            empty_annotation_sample_ratio=context.get(
                Ctx.EMPTY_ANNOTATION_SAMPLE_RATIO
            ),
        ),
        lineage=PipelineResultLineage(
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            model_id=pipeline_run.model_id,
            model_version=pipeline_run.model_version,
            dataset_manifest_uri=context.get(Ctx.DATASET_MANIFEST_URI),
            validation_run_id=context.get(Ctx.VALIDATION_RUN_ID),
            validation_report_uri=context.get(Ctx.VALIDATION_REPORT_URI),
            inference_run_id=context.get(Ctx.INFERENCE_RUN_ID),
            prediction_manifest_uri=context.get(Ctx.PREDICTION_MANIFEST_URI),
            evaluation_run_id=context.get(Ctx.EVALUATION_RUN_ID),
            evaluation_manifest_uri=context.get(Ctx.EVALUATION_MANIFEST_URI),
            profile_report_uri=context.get(Ctx.PROFILE_REPORT_URI),
            profile_run_id=context.get(Ctx.PROFILE_RUN_ID),
        ),
        outputs=PipelineResultOutputs(
            build_scenes=_build_build_scenes_output(context),
            dataset=_build_dataset_output(context),
            validation=_build_validation_output(context),
            profile=_build_profile_output(context),
            inference=_build_inference_output(context),
            evaluation=_build_evaluation_output(context),
        ),
        steps=steps,
    )


def _build_build_scenes_output(context: Any) -> PipelineBuildScenesOutput | None:
    if context.get(Ctx.BUILD_SCENES_RAW_LOG_ID) is None:
        return None

    return PipelineBuildScenesOutput(
        raw_log_id=context.get(Ctx.BUILD_SCENES_RAW_LOG_ID),
        raw_log_manifest_uri=context.get(Ctx.BUILD_SCENES_RAW_LOG_MANIFEST_URI),
        scene_segments_uri=context.get(Ctx.BUILD_SCENES_SCENE_SEGMENTS_URI),
        scene_index_uri=context.get(Ctx.BUILD_SCENES_SCENE_INDEX_URI),
        frame_count=context.get(Ctx.BUILD_SCENES_FRAME_COUNT),
        scene_count=context.get(Ctx.SCENE_COUNT),
        sample_count=context.get(Ctx.SAMPLE_COUNT),
        channels=context.get(Ctx.BUILD_SCENES_CHANNELS) or [],
    )


# XXX refactor to Ctx.
def _build_dataset_output(context: Any) -> PipelineDatasetOutput | None:
    if context.get("dataset_manifest_uri") is None:
        return None

    return PipelineDatasetOutput(
        manifest_uri=context.get("dataset_manifest_uri"),
        scene_count=context.get("scene_count"),
        sample_count=context.get("sample_count"),
        annotation_count=context.get("annotation_count"),
    )


def _build_validation_output(context: Any) -> PipelineValidationOutput | None:
    if context.get("validation_run_id") is None:
        return None

    return PipelineValidationOutput(
        run_id=context.get("validation_run_id"),
        status=context.get("validation_status"),
        scope=context.get("validation_scope"),
        report_uri=context.get("validation_report_uri"),
        should_block_pipeline=context.get("should_block_pipeline"),
        validated_scene_count=context.get("validated_scene_count"),
        validated_sample_count=context.get("validated_sample_count"),
        issue_count=context.get("validation_issue_count"),
        error_count=context.get("validation_error_count"),
        warning_count=context.get("validation_warning_count"),
        missing_scene_count=context.get("missing_scene_count"),
        missing_sample_count=context.get("missing_sample_count"),
        missing_channel_count=context.get("missing_channel_count"),
        missing_artifact_count=context.get("missing_artifact_count"),
    )


def _build_inference_output(context: Any) -> PipelineInferenceOutput | None:
    if context.get("inference_run_id") is None:
        return None

    return PipelineInferenceOutput(
        run_id=context.get("inference_run_id"),
        prediction_manifest_uri=context.get("prediction_manifest_uri"),
    )


def _build_evaluation_output(context: Any) -> PipelineEvaluationOutput | None:
    if context.get("evaluation_run_id") is None:
        return None

    return PipelineEvaluationOutput(
        run_id=context.get("evaluation_run_id"),
        evaluation_manifest_uri=context.get("evaluation_manifest_uri"),
        metrics=context.get("metrics"),
    )


def _build_profile_output(context: Any) -> PipelineProfileOutput | None:
    if context.get("profile_run_id") is None:
        return None

    return PipelineProfileOutput(
        run_id=context.get("profile_run_id"),
        report_uri=context.get("profile_report_uri"),
        profiled_scene_count=context.get("profiled_scene_count"),
        profiled_sample_count=context.get("profiled_sample_count"),
        observed_channels=context.get("observed_channels") or [],
        observed_channel_count=context.get("observed_channel_count"),
        missing_required_channel_count=context.get("missing_required_channel_count"),
        sensor_coverage_ratio=context.get("sensor_coverage_ratio"),
        empty_annotation_sample_count=context.get("empty_annotation_sample_count"),
        empty_annotation_sample_ratio=context.get("empty_annotation_sample_ratio"),
    )


def build_pipeline_step_result(
    *,
    step: PipelineStepRunManifest,
    job: JobManifest | None,
) -> PipelineStepResult:
    job_result = job.result if job is not None else None

    return PipelineStepResult(
        step_name=step.step_name,
        job_type=step.job_type.value,
        job_id=step.job_id,
        status=step.status.value,
        result=_compact_step_result(
            step_name=step.step_name,
            job_result=job_result,
        ),
        error=step.error,
    )


def _compact_step_result(
    *,
    step_name: str,
    job_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if job_result is None:
        return {}

    raw = job_result.get("job_result", job_result)

    if step_name == "build_scenes":
        return {
            "raw_log_id": raw.get("raw_log_id"),
            "raw_log_manifest_uri": raw.get("raw_log_manifest_uri"),
            "scene_segments_uri": raw.get("scene_segments_uri"),
            "scene_index_uri": raw.get("scene_index_uri"),
            "dataset_manifest_uri": raw.get("dataset_manifest_uri"),
            "scene_count": raw.get("scene_count"),
            "sample_count": raw.get("sample_count"),
            "frame_count": raw.get("frame_count"),
            "channels": raw.get("channels", []),
        }

    if step_name == "ingest":
        return {
            "dataset_manifest_uri": raw.get("dataset_manifest_uri"),
            "scene_count": raw.get("scene_count"),
            "sample_count": raw.get("sample_count"),
            "annotation_count": raw.get("annotation_count"),
        }

    if step_name == "validate":
        return {
            "validation_run_id": raw.get("validation_run_id"),
            "validation_status": raw.get("status")
            or raw.get("validation_status")
            or raw.get("result_summary", {}).get("validation_status"),
            "validation_report_uri": raw.get("validation_report_uri"),
            "should_block_pipeline": raw.get("should_block_pipeline"),
            "issue_count": raw.get("issue_count"),
            "error_count": raw.get("error_count"),
            "warning_count": raw.get("warning_count"),
        }

    if step_name == "predict":
        return {
            "inference_run_id": raw.get("inference_run_id"),
            "prediction_manifest_uri": raw.get("prediction_manifest_uri"),
        }

    if step_name == "evaluate":
        return {
            "evaluation_run_id": raw.get("evaluation_run_id"),
            "evaluation_manifest_uri": raw.get("evaluation_manifest_uri"),
            "metrics": raw.get("metrics"),
        }

    return raw
