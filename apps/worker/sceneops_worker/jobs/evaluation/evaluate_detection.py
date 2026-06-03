from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.ids import default_evaluation_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.jobs.schemas import (
    EvaluateDetectionJobParams,
    EvaluateDetectionJobResult,
    JobType,
)
from sceneops_core.runs.schemas import EvaluationRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.evaluation import create_detection_evaluator
from sceneops_worker.evaluation.detection import DetectionEvaluationRequest
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.jobs.context import JobContext
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
            id=evaluation_run_id,
            inference_run_id=params.inference_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id="",  # resolved during execute once inference run is loaded
            model_version="",
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
        context: JobContext,
        initial_record: EvaluationRunRecord,
        started_at: datetime,
    ) -> tuple[EvaluationRunRecord, EvaluateDetectionJobResult]:
        evaluation_run_id = initial_record.id

        version = await context.dataset_registry_store.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
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

        inference_run = await context.run_registry_store.get_inference_run(
            params.inference_run_id
        )

        dataset_manifest = await context.dataset_artifact_store.load_dataset_manifest(
            version.manifest_uri
        )

        # patch model identity into initial record now that we have the inference run
        await context.run_registry_store.upsert_evaluation_run(
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

        metrics = evaluation_manifest.get("metrics", {})
        class_metrics = evaluation_manifest.get("class_metrics", {})
        sample_count = evaluation_manifest.get("sample_count")
        evaluation_manifest_uri = evaluation_manifest["evaluation_manifest_uri"]
        samples_root_uri = evaluation_manifest.get("samples_root_uri")

        succeeded_record = initial_record.model_copy(
            update={
                "model_id": inference_run.model_id,
                "model_version": inference_run.model_version,
                "status": RunStatus.SUCCEEDED,
                "sample_count": sample_count,
                "evaluation_manifest_uri": evaluation_manifest_uri,
                "samples_root_uri": samples_root_uri,
                "metrics": metrics,
                "class_metrics": class_metrics,
                "finished_at": utc_now(),
            }
        )

        job_result = EvaluateDetectionJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            inference_run_id=params.inference_run_id,
            evaluation_run_id=evaluation_run_id,
            evaluation_manifest_uri=evaluation_manifest_uri,
            metrics=metrics,
            sample_count=sample_count,
            result_summary={
                "status": evaluation_manifest.get("status"),
                "match_distance_m": evaluation_manifest.get("match_distance_m"),
                "class_metrics": class_metrics,
                "samples_root_uri": samples_root_uri,
                "created_at": evaluation_manifest.get("created_at"),
            },
        )

        return succeeded_record, job_result

    async def _upsert(self, context: JobContext, record: EvaluationRunRecord) -> None:
        await context.run_registry_store.upsert_evaluation_run(record)
