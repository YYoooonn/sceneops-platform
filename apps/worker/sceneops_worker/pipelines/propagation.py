from __future__ import annotations

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.jobs import (
    EvaluateDetectionJobResult,
    IngestDatasetJobResult,
    JobType,
    PredictDetectionJobResult,
    ValidateDatasetManifestJobResult,
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
        job_type = JobType(step.job_type)

        if job_type == JobType.INGEST_DATASET:
            parsed = IngestDatasetJobResult.model_validate(result)
            context.set("dataset_manifest_uri", parsed.dataset_manifest_uri)
            context.set("scene_count", parsed.scene_count)
            context.set("sample_count", parsed.sample_count)
            return

        if job_type == JobType.VALIDATE_DATASET_MANIFEST:
            parsed = ValidateDatasetManifestJobResult.model_validate(result)
            context.set("dataset_manifest_uri", parsed.dataset_manifest_uri)
            context.set("validated_scene_count", parsed.validated_scene_count)
            context.set("validated_sample_count", parsed.validated_sample_count)
            context.set("dataset_status", parsed.status)
            return

        if job_type == JobType.PREDICT_DETECTION:
            parsed = PredictDetectionJobResult.model_validate(result)
            context.set("inference_run_id", parsed.inference_run_id)
            context.set("prediction_manifest_uri", parsed.prediction_manifest_uri)
            context.set("prediction_sample_count", parsed.sample_count)
            return

        if job_type == JobType.EVALUATE_DETECTION:
            parsed = EvaluateDetectionJobResult.model_validate(result)
            context.set("evaluation_run_id", parsed.evaluation_run_id)
            context.set("evaluation_manifest_uri", parsed.evaluation_manifest_uri)
            context.set("metrics", parsed.metrics)
            context.set("evaluation_sample_count", parsed.sample_count)
            return
