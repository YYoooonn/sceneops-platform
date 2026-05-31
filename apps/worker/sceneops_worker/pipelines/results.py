from __future__ import annotations

from typing import Any

from sceneops_core.schemas.jobs import JobManifest
from sceneops_core.schemas.pipelines import (
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
)
from sceneops_worker.pipelines.context import PipelineExecutionContext


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
            dataset_status=context.get("dataset_status"),
            validation_status=context.get("validation_status"),
            should_block_pipeline=context.get("should_block_pipeline"),
            scene_count=context.get("scene_count"),
            sample_count=context.get("sample_count"),
            annotation_count=context.get("annotation_count"),
            validated_scene_count=context.get("validated_scene_count"),
            validated_sample_count=context.get("validated_sample_count"),
            issue_count=context.get("validation_issue_count"),
            error_count=context.get("validation_error_count"),
            warning_count=context.get("validation_warning_count"),
            metrics=context.get("metrics"),
        ),
        lineage=PipelineResultLineage(
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            model_id=pipeline_run.model_id,
            model_version=pipeline_run.model_version,
            dataset_manifest_uri=context.get("dataset_manifest_uri"),
            validation_run_id=context.get("validation_run_id"),
            validation_report_uri=context.get("validation_report_uri"),
            inference_run_id=context.get("inference_run_id"),
            prediction_manifest_uri=context.get("prediction_manifest_uri"),
            evaluation_run_id=context.get("evaluation_run_id"),
            evaluation_manifest_uri=context.get("evaluation_manifest_uri"),
        ),
        outputs=PipelineResultOutputs(
            dataset=_build_dataset_output(context),
            validation=_build_validation_output(context),
            inference=_build_inference_output(context),
            evaluation=_build_evaluation_output(context),
        ),
        steps=steps,
    )


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
