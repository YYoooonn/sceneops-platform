from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    EvaluateDetectionJobResult,
    IngestDatasetJobResult,
    JobType,
    PredictDetectionJobResult,
    ProfileDatasetJobResult,
    ValidateDatasetJobResult,
)
from sceneops_core.pipelines.schemas import PipelineStepRunManifest
from sceneops_worker.pipelines.context import PipelineExecutionContext
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


class PipelineResultPropagator:
    def apply_step_result(
        self,
        *,
        step: PipelineStepRunManifest,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        if step.job_type == JobType.INGEST_DATASET:
            self._apply_ingest_result(result=result, context=context)
            return

        if step.job_type == JobType.VALIDATE_DATASET:
            self._apply_validation_result(result=result, context=context)
            return

        if step.job_type == JobType.PROFILE_DATASET:
            self._apply_profile_result(result=result, context=context)
            return

        if step.job_type == JobType.PREDICT_DETECTION:
            self._apply_prediction_result(result=result, context=context)
            return

        if step.job_type == JobType.EVALUATE_DETECTION:
            self._apply_evaluation_result(result=result, context=context)
            return

    def _apply_profile_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = ProfileDatasetJobResult.model_validate(result)

        context.set(Ctx.DATASET_ID, parsed.dataset_id)
        context.set(Ctx.DATASET_VERSION, parsed.dataset_version)
        context.set(Ctx.DATASET_MANIFEST_URI, parsed.dataset_version)

        context.set(Ctx.PROFILE_RUN_ID, parsed.profile_run_id)
        context.set(Ctx.PROFILE_REPORT_URI, parsed.profile_report_uri)

        # Dataset summary counts.
        context.set(Ctx.SCENE_COUNT, parsed.scene_count)
        context.set(Ctx.SAMPLE_COUNT, parsed.sample_count)
        context.set(Ctx.ANNOTATION_COUNT, parsed.annotation_count)

        # Profile summary.
        context.set(Ctx.PROFILE_SCENE_COUNT, parsed.profiled_scene_count)
        context.set(Ctx.PROFILE_SAMPLE_COUNT, parsed.profiled_sample_count)

        context.set(Ctx.OBSERVED_CHANNELS, parsed.observed_channels)
        context.set(Ctx.OBSERVED_CHANNEL_COUNT, len(parsed.observed_channels))

        context.set(
            Ctx.MISSING_REQUIRED_CHANNEL_COUNT,
            parsed.missing_required_channel_count,
        )
        context.set(Ctx.SENSOR_COVERAGE_RATIO, parsed.sensor_coverage_ratio)

        context.set(
            Ctx.EMPTY_ANNOTATION_SAMPLE_COUNT,
            parsed.empty_annotation_sample_count,
        )
        context.set(
            Ctx.EMPTY_ANNOTATION_SAMPLE_RATIO,
            parsed.empty_annotation_sample_ratio,
        )

        context.set(Ctx.PROFILE_SUMMARY, parsed.result_summary)

    def _apply_ingest_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = IngestDatasetJobResult.model_validate(result)

        context.set(Ctx.DATASET_ID, parsed.dataset_id)
        context.set(Ctx.DATASET_VERSION, parsed.dataset_version)
        context.set(Ctx.DATASET_MANIFEST_URI, parsed.dataset_manifest_uri)

        # Counts for summary / dataset output.
        context.set(Ctx.SCENE_COUNT, parsed.scene_count)
        context.set(Ctx.SAMPLE_COUNT, parsed.sample_count)

        # Optional fields depending on your schema.
        if hasattr(parsed, "annotation_count"):
            context.set(Ctx.ANNOTATION_COUNT, parsed.annotation_count)

        if hasattr(parsed, "dataset_type"):
            context.set(Ctx.DATASET_TYPE, parsed.dataset_type)

        # Ingest result status can be used as dataset_status.
        # If your IngestDatasetJobResult does not have `status`,
        # fall back to result_summary.status.
        dataset_status = None
        if hasattr(parsed, "status"):
            dataset_status = parsed.status
        elif parsed.result_summary:
            dataset_status = parsed.result_summary.get("status")

        if dataset_status is not None:
            context.set(Ctx.DATASET_STATUS, _enum_or_value(dataset_status))

    def _apply_validation_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = ValidateDatasetJobResult.model_validate(result)

        context.set(Ctx.DATASET_ID, parsed.dataset_id)
        context.set(Ctx.DATASET_VERSION, parsed.dataset_version)
        context.set(Ctx.DATASET_MANIFEST_URI, parsed.dataset_manifest_uri)

        context.set(Ctx.VALIDATION_RUN_ID, parsed.validation_run_id)
        context.set(Ctx.VALIDATION_REPORT_URI, parsed.validation_report_uri)
        context.set(Ctx.VALIDATION_STATUS, _enum_or_value(parsed.status))
        context.set(Ctx.VALIDATION_SCOPE, _enum_or_value(parsed.validation_scope))
        context.set(Ctx.SHOULD_BLOCK_PIPELINE, parsed.should_block_pipeline)

        # Dataset summary counts.
        context.set(Ctx.SCENE_COUNT, parsed.scene_count)
        context.set(Ctx.SAMPLE_COUNT, parsed.sample_count)
        context.set(Ctx.ANNOTATION_COUNT, parsed.annotation_count)

        # Validation summary counts.
        context.set(Ctx.VALIDATED_SCENE_COUNT, parsed.validated_scene_count)
        context.set(Ctx.VALIDATED_SAMPLE_COUNT, parsed.validated_sample_count)

        context.set(Ctx.VALIDATION_ISSUE_COUNT, parsed.issue_count)
        context.set(Ctx.VALIDATION_ERROR_COUNT, parsed.error_count)
        context.set(Ctx.VALIDATION_WARNING_COUNT, parsed.warning_count)

        context.set(Ctx.MISSING_SCENE_COUNT, parsed.missing_scene_count)
        context.set(Ctx.MISSING_SAMPLE_COUNT, parsed.missing_sample_count)
        context.set(Ctx.MISSING_CHANNEL_COUNT, parsed.missing_channel_count)
        context.set(Ctx.MISSING_ARTIFACT_COUNT, parsed.missing_artifact_count)

        # Optional aliases for result builder readability.
        context.set(Ctx.ISSUE_COUNT, parsed.issue_count)
        context.set(Ctx.ERROR_COUNT, parsed.error_count)
        context.set(Ctx.WARNING_COUNT, parsed.warning_count)

    def _apply_prediction_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = PredictDetectionJobResult.model_validate(result)

        context.set(Ctx.INFERENCE_RUN_ID, parsed.inference_run_id)
        context.set(Ctx.PREDICTION_MANIFEST_URI, parsed.prediction_manifest_uri)
        context.set(Ctx.PREDICTION_SAMPLE_COUNT, parsed.sample_count)

        if hasattr(parsed, "model_id"):
            context.set(Ctx.PREDICTION_MODEL_ID, parsed.model_id)

        if hasattr(parsed, "model_version"):
            context.set(Ctx.PREDICTION_MODEL_VERSION, parsed.model_version)

        if hasattr(parsed, "inference_backend"):
            context.set(Ctx.INFERENCE_BACKEND, _enum_or_value(parsed.inference_backend))

    def _apply_evaluation_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = EvaluateDetectionJobResult.model_validate(result)

        context.set(Ctx.EVALUATION_RUN_ID, parsed.evaluation_run_id)
        context.set(Ctx.EVALUATION_MANIFEST_URI, parsed.evaluation_manifest_uri)
        context.set(Ctx.EVALUATION_METRICS, parsed.metrics)
        context.set(Ctx.EVALUATION_SAMPLE_COUNT, parsed.sample_count)

        if hasattr(parsed, "model_id"):
            context.set(Ctx.EVALUATION_MODEL_ID, parsed.model_id)

        if hasattr(parsed, "model_version"):
            context.set(Ctx.EVALUATION_MODEL_VERSION, parsed.model_version)


def _enum_or_value(value):
    if hasattr(value, "value"):
        return value.value

    return value
