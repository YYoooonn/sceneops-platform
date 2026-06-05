from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_evaluation_run_id, generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.evaluations.schemas import EvaluationTaskType
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.jobs.schemas import (
    EvaluateDetectionJobParams,
    EvaluateDetectionJobResult,
    JobType,
)
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.evaluation import create_detection_evaluator
from sceneops_worker.evaluation.detection import DetectionEvaluationRequest
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


class EvaluateDetectionJobHandler(
    RunRecordHandler[
        EvaluateDetectionJobParams, EvaluateDetectionJobResult, EvaluationRunRecord
    ],
    JobHandler[EvaluateDetectionJobParams, EvaluateDetectionJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.EVALUATE_DETECTION

    @property
    def params_model(self) -> type[EvaluateDetectionJobParams]:
        return EvaluateDetectionJobParams

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        inference_run_id = base.get("inference_run_id") or context_values.get(
            Ctx.INFERENCE_RUN_ID
        )
        if inference_run_id is None:
            raise ValueError("inference_run_id is required for evaluation step")
        return {**base, "inference_run_id": inference_run_id}

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = EvaluateDetectionJobResult.model_validate(result)
        return {
            Ctx.EVALUATION_RUN_ID: parsed.evaluation_run_id,
            Ctx.EVALUATION_MANIFEST_URI: parsed.evaluation_manifest_uri,
            Ctx.EVALUATION_METRICS: parsed.metrics,
            Ctx.EVALUATION_SAMPLE_COUNT: parsed.sample_count,
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: EvaluateDetectionJobParams,
        started_at: datetime,
    ) -> EvaluationRunRecord:
        evaluation_run_id = params.evaluation_run_id or default_evaluation_run_id(
            job.job_id
        )
        return EvaluationRunRecord(
            run_id=evaluation_run_id,
            inference_run_id=params.inference_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            task_type=EvaluationTaskType.DETECTION,
            evaluator_id=params.evaluator_id,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_step_run_id=job.pipeline_step_run_id,
            job_id=job.job_id,
            metadata={"match_distance_m": params.match_distance_m},
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: EvaluateDetectionJobParams,
        context: WorkerContext,
        initial_record: EvaluationRunRecord,
        started_at: datetime,
    ) -> tuple[EvaluationRunRecord, EvaluateDetectionJobResult]:
        evaluation_run_id = initial_record.run_id

        version = await context.dataset_store.get_version(
            dataset_id=params.dataset_id,
            version=params.dataset_version,
        )

        if version is None:
            raise ValueError(
                f"Dataset version not found: {params.dataset_id}:{params.dataset_version}"
            )

        if version.status != DatasetVersionStatus.READY:
            raise ValueError(
                f"Dataset version is not usable for evaluation: "
                f"{params.dataset_id}:{params.dataset_version}, "
                f"status={version.status}"
            )

        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        inference_run = await context.runs.inference.get(params.inference_run_id)
        if inference_run is None:
            raise ValueError(f"Inference run not found: {params.inference_run_id}")

        dataset_manifest = await context.dataset_artifact_store.load_dataset_manifest(
            version.manifest_uri
        )

        # Patch model identity onto the initial record now that we have the inference run.
        await context.runs.evaluations.save(
            initial_record.model_copy(
                update={
                    "model_id": inference_run.model_id,
                    "model_version": inference_run.model_version,
                }
            )
        )

        evaluator = create_detection_evaluator(params.evaluator_id)
        evaluation_manifest = await evaluator.run(
            DetectionEvaluationRequest(
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=context.dataset_artifact_store,
                run_artifact_store=context.run_artifact_store,
                inference_run_id=params.inference_run_id,
                evaluation_run_id=evaluation_run_id,
                match_distance_m=params.match_distance_m,
            )
        )

        # evaluation_manifest is now a typed DetectionEvaluationManifest
        metrics = evaluation_manifest.metrics
        class_metrics = evaluation_manifest.class_metrics
        sample_count = evaluation_manifest.sample_count
        prediction_count = evaluation_manifest.prediction_count
        ground_truth_count = evaluation_manifest.ground_truth_count
        evaluation_unit = evaluation_manifest.evaluation_unit or "annotation"
        evaluation_manifest_uri = evaluation_manifest.evaluation_manifest_uri
        primary_metric_name = evaluation_manifest.primary_metric_name
        primary_metric_value = evaluation_manifest.primary_metric_value

        # Write a separate metrics.json artifact.
        metrics_uri = await context.run_artifact_store.write_evaluation_run_metrics(
            evaluation_run_id=evaluation_run_id,
            metrics={
                "evaluation_run_id": evaluation_run_id,
                "primary_metric_name": primary_metric_name,
                "primary_metric_value": primary_metric_value,
                "metrics": metrics,
                "class_metrics": class_metrics,
                "sample_count": sample_count,
                "prediction_count": prediction_count,
                "ground_truth_count": ground_truth_count,
                "evaluation_unit": evaluation_unit,
            },
        )

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.EVALUATION_MANIFEST,
                uri=evaluation_manifest_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.EVALUATION_RUN,
            owner_id=evaluation_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=evaluation_run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.METRICS,
                uri=metrics_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.EVALUATION_RUN,
            owner_id=evaluation_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=evaluation_run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        succeeded_record = initial_record.model_copy(
            update={
                "model_id": inference_run.model_id,
                "model_version": inference_run.model_version,
                "status": RunStatus.SUCCEEDED,
                "sample_count": sample_count,
                "prediction_count": prediction_count,
                "ground_truth_count": ground_truth_count,
                "evaluation_unit": evaluation_unit,
                "primary_metric_name": primary_metric_name,
                "primary_metric_value": primary_metric_value,
                "evaluation_manifest_uri": evaluation_manifest_uri,
                "metrics_uri": metrics_uri,
                "metrics": metrics,
                "class_metrics": class_metrics,
                "summary": {
                    "status": evaluation_manifest.status,
                    "match_distance_m": evaluation_manifest.match_distance_m,
                    "samples_root_uri": evaluation_manifest.samples_root_uri,
                },
                "finished_at": utc_now(),
            }
        )

        job_result = EvaluateDetectionJobResult(
            evaluation_run_id=evaluation_run_id,
            evaluation_manifest_uri=evaluation_manifest_uri,
            metrics_uri=metrics_uri,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=inference_run.model_id,
            model_version=inference_run.model_version,
            inference_run_id=params.inference_run_id,
            sample_count=sample_count,
            prediction_count=prediction_count,
            ground_truth_count=ground_truth_count,
            evaluation_unit=evaluation_unit,
            primary_metric_name=primary_metric_name,
            primary_metric_value=primary_metric_value,
            metrics=metrics,
            class_metrics=class_metrics,
            summary={
                "status": evaluation_manifest.status,
                "match_distance_m": evaluation_manifest.match_distance_m,
                "samples_root_uri": evaluation_manifest.samples_root_uri,
            },
            metadata={},
        )

        return succeeded_record, job_result

    async def _upsert(
        self, context: WorkerContext, record: EvaluationRunRecord
    ) -> EvaluationRunRecord:
        return await context.runs.evaluations.upsert(record)
