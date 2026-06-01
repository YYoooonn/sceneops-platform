from __future__ import annotations

from sceneops_core.common.ids import default_evaluation_run_id
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.jobs.schemas import (
    EvaluateDetectionJobParams,
    EvaluateDetectionJobResult,
    JobManifest,
    JobType,
)
from sceneops_core.runs.schemas import EvaluationRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.evaluation.detection import evaluate_detection_run
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class EvaluateDetectionJobHandler(
    TypedJobHandler[EvaluateDetectionJobParams, EvaluateDetectionJobResult]
):
    job_type = JobType.EVALUATE_DETECTION

    def parse_params(self, job: JobManifest) -> EvaluateDetectionJobParams:
        return EvaluateDetectionJobParams.model_validate(job.params)

    async def run(
        self,
        *,
        params: EvaluateDetectionJobParams,
        job: JobManifest,
    ) -> EvaluateDetectionJobResult:
        if params.evaluator_id == "center-distance":
            return await self._run_center_distance(params=params, job=job)

        raise ValueError(f"Unsupported evaluator: {params.evaluator_id}")

    async def _run_center_distance(
        self,
        *,
        params: EvaluateDetectionJobParams,
        job: JobManifest,
    ) -> EvaluateDetectionJobResult:
        version = await self.context.dataset_registry_store.get_version(
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

        inference_run = await self.context.run_registry_store.get_inference_run(
            params.inference_run_id
        )

        dataset_manifest = (
            await self.context.dataset_artifact_store.load_dataset_manifest(
                version.manifest_uri
            )
        )

        evaluation_run_id = params.evaluation_run_id or default_evaluation_run_id(
            job.job_id
        )

        started_at = utc_now()

        await self.context.run_registry_store.upsert_evaluation_run(
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
            evaluation_manifest = await evaluate_detection_run(
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=self.context.dataset_artifact_store,
                run_artifact_store=self.context.run_artifact_store,
                inference_run_id=params.inference_run_id,
                evaluation_run_id=evaluation_run_id,
                match_distance_m=params.match_distance_m,
            )

            metrics = evaluation_manifest.get("metrics", {})
            class_metrics = evaluation_manifest.get("classMetrics", {})
            sample_count = evaluation_manifest.get("sampleCount")
            evaluation_manifest_uri = evaluation_manifest["evaluationManifestUri"]
            samples_root_uri = evaluation_manifest.get("samplesRootUri")

            await self.context.run_registry_store.upsert_evaluation_run(
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
            await self.context.run_registry_store.upsert_evaluation_run(
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
