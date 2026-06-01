from __future__ import annotations

from sceneops_core.common.ids import default_evaluation_run_id
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
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class EvaluateDetectionJobHandler(
    JobHandler[EvaluateDetectionJobParams, EvaluateDetectionJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.EVALUATE_DETECTION

    @property
    def params_model(self) -> type[EvaluateDetectionJobParams]:
        return EvaluateDetectionJobParams

    async def run(
        self, request: JobHandlerRequest[EvaluateDetectionJobParams]
    ) -> EvaluateDetectionJobResult:
        job = request.job
        params = request.params
        context = request.context

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

        evaluation_run_id = params.evaluation_run_id or default_evaluation_run_id(
            job.job_id
        )

        started_at = utc_now()

        await context.run_registry_store.upsert_evaluation_run(
            EvaluationRunRecord(
                id=evaluation_run_id,
                inference_run_id=params.inference_run_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                model_id=inference_run.model_id,
                model_version=inference_run.model_version,
                evaluator_id=params.evaluator_id,
                status=RunStatus.RUNNING,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_step_run_id=job.pipeline_step_run_id,
                job_id=job.job_id,
                metadata={
                    "match_distance_m": params.match_distance_m,
                },
                started_at=started_at,
            )
        )

        try:
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
            class_metrics = evaluation_manifest.get("classMetrics", {})
            sample_count = evaluation_manifest.get("sampleCount")
            evaluation_manifest_uri = evaluation_manifest["evaluationManifestUri"]
            samples_root_uri = evaluation_manifest.get("samplesRootUri")

            await context.run_registry_store.upsert_evaluation_run(
                EvaluationRunRecord(
                    id=evaluation_run_id,
                    inference_run_id=params.inference_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    model_id=evaluation_manifest["modelId"],
                    model_version=evaluation_manifest["modelVersion"],
                    evaluator_id=params.evaluator_id,
                    status=RunStatus.SUCCEEDED,
                    sample_count=sample_count,
                    evaluation_manifest_uri=evaluation_manifest_uri,
                    samples_root_uri=samples_root_uri,
                    metrics=metrics,
                    class_metrics=class_metrics,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    metadata={
                        "match_distance_m": params.match_distance_m,
                    },
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )

            return EvaluateDetectionJobResult(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                inference_run_id=params.inference_run_id,
                evaluation_run_id=evaluation_run_id,
                evaluation_manifest_uri=evaluation_manifest_uri,
                metrics=metrics,
                sample_count=sample_count,
                result_summary={
                    "status": evaluation_manifest.get("status"),
                    "match_distance_m": evaluation_manifest.get("matchDistanceM"),
                    "class_metrics": class_metrics,
                    "samples_root_uri": samples_root_uri,
                    "created_at": evaluation_manifest.get("createdAt"),
                },
            )

        except Exception as error:
            await context.run_registry_store.upsert_evaluation_run(
                EvaluationRunRecord(
                    id=evaluation_run_id,
                    inference_run_id=params.inference_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    model_id=inference_run.model_id,
                    model_version=inference_run.model_version,
                    evaluator_id=params.evaluator_id,
                    status=RunStatus.FAILED,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    metadata={
                        "match_distance_m": params.match_distance_m,
                    },
                    error={
                        "type": error.__class__.__name__,
                        "message": str(error),
                        "details": {},
                    },
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )
            raise
