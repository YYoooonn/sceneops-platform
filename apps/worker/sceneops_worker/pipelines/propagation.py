from __future__ import annotations

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.jobs import (
    EvaluateDetectionJobResult,
    IngestDatasetJobResult,
    JobType,
    PredictDetectionJobResult,
    ValidateDatasetJobResult,
)
from sceneops_core.schemas.pipelines import PipelineStepRunManifest
from sceneops_worker.pipelines.context import PipelineExecutionContext


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

        if step.job_type == JobType.PREDICT_DETECTION:
            self._apply_prediction_result(result=result, context=context)
            return

        if step.job_type == JobType.EVALUATE_DETECTION:
            self._apply_evaluation_result(result=result, context=context)
            return

    def _apply_ingest_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = IngestDatasetJobResult.model_validate(result)

        context.set("dataset_id", parsed.dataset_id)
        context.set("dataset_version", parsed.dataset_version)
        context.set("dataset_manifest_uri", parsed.dataset_manifest_uri)

        # Counts for summary / dataset output.
        context.set("scene_count", parsed.scene_count)
        context.set("sample_count", parsed.sample_count)

        # Optional fields depending on your schema.
        if hasattr(parsed, "annotation_count"):
            context.set("annotation_count", parsed.annotation_count)

        if hasattr(parsed, "dataset_type"):
            context.set("dataset_type", parsed.dataset_type)

        # Ingest result status can be used as dataset_status.
        # If your IngestDatasetJobResult does not have `status`,
        # fall back to result_summary.status.
        dataset_status = None
        if hasattr(parsed, "status"):
            dataset_status = parsed.status
        elif parsed.result_summary:
            dataset_status = parsed.result_summary.get("status")

        if dataset_status is not None:
            context.set("dataset_status", _enum_or_value(dataset_status))

    def _apply_validation_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = ValidateDatasetJobResult.model_validate(result)

        context.set("dataset_id", parsed.dataset_id)
        context.set("dataset_version", parsed.dataset_version)
        context.set("dataset_manifest_uri", parsed.dataset_manifest_uri)

        context.set("validation_run_id", parsed.validation_run_id)
        context.set("validation_report_uri", parsed.validation_report_uri)
        context.set("validation_status", _enum_or_value(parsed.status))
        context.set("validation_scope", _enum_or_value(parsed.validation_scope))
        context.set("should_block_pipeline", parsed.should_block_pipeline)

        # Dataset summary counts.
        context.set("scene_count", parsed.scene_count)
        context.set("sample_count", parsed.sample_count)
        context.set("annotation_count", parsed.annotation_count)

        # Validation summary counts.
        context.set("validated_scene_count", parsed.validated_scene_count)
        context.set("validated_sample_count", parsed.validated_sample_count)

        context.set("validation_issue_count", parsed.issue_count)
        context.set("validation_error_count", parsed.error_count)
        context.set("validation_warning_count", parsed.warning_count)

        context.set("missing_scene_count", parsed.missing_scene_count)
        context.set("missing_sample_count", parsed.missing_sample_count)
        context.set("missing_channel_count", parsed.missing_channel_count)
        context.set("missing_artifact_count", parsed.missing_artifact_count)

        # Optional aliases for result builder readability.
        context.set("issue_count", parsed.issue_count)
        context.set("error_count", parsed.error_count)
        context.set("warning_count", parsed.warning_count)

    def _apply_prediction_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = PredictDetectionJobResult.model_validate(result)

        context.set("inference_run_id", parsed.inference_run_id)
        context.set("prediction_manifest_uri", parsed.prediction_manifest_uri)
        context.set("prediction_sample_count", parsed.sample_count)

        if hasattr(parsed, "model_id"):
            context.set("model_id", parsed.model_id)

        if hasattr(parsed, "model_version"):
            context.set("model_version", parsed.model_version)

        if hasattr(parsed, "inference_backend"):
            context.set("inference_backend", _enum_or_value(parsed.inference_backend))

    def _apply_evaluation_result(
        self,
        *,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        parsed = EvaluateDetectionJobResult.model_validate(result)

        context.set("evaluation_run_id", parsed.evaluation_run_id)
        context.set("evaluation_manifest_uri", parsed.evaluation_manifest_uri)
        context.set("metrics", parsed.metrics)
        context.set("evaluation_sample_count", parsed.sample_count)

        if hasattr(parsed, "model_id"):
            context.set("model_id", parsed.model_id)

        if hasattr(parsed, "model_version"):
            context.set("model_version", parsed.model_version)


def _enum_or_value(value):
    if hasattr(value, "value"):
        return value.value

    return value
