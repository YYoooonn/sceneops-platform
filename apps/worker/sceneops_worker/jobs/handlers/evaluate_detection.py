from __future__ import annotations

from sceneops_core.ids.runs import default_evaluation_run_id
from sceneops_core.schemas.datasets import DatasetVersionStatus
from sceneops_core.schemas.jobs import (
    EvaluateDetectionJobParams,
    EvaluateDetectionJobResult,
    JobManifest,
    JobType,
)
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

        if version.status not in {
            DatasetVersionStatus.INGESTED,
            # DatasetVersionStatus.READY,
        }:
            raise ValueError(
                f"Dataset version is not usable for evaluation: "
                f"{params.dataset_id}:{params.dataset_version}, status={version.status}"
            )

        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        dataset_manifest = (
            await self.context.dataset_artifact_store.load_dataset_manifest(
                version.manifest_uri
            )
        )

        evaluation_run_id = params.evaluation_run_id or default_evaluation_run_id(
            job.job_id
        )

        evaluation_manifest = await evaluate_detection_run(
            dataset_manifest=dataset_manifest,
            dataset_artifact_store=self.context.dataset_artifact_store,
            run_artifact_store=self.context.run_artifact_store,
            inference_run_id=params.inference_run_id,
            evaluation_run_id=evaluation_run_id,
            match_distance_m=params.match_distance_m,
        )

        return EvaluateDetectionJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            inference_run_id=params.inference_run_id,
            evaluation_run_id=evaluation_run_id,
            evaluation_manifest_uri=evaluation_manifest["evaluationManifestUri"],
            metrics=evaluation_manifest.get("metrics", {}),
            sample_count=evaluation_manifest.get("sampleCount"),
            result_summary={
                "status": evaluation_manifest.get("status"),
                "match_distance_m": evaluation_manifest.get("matchDistanceM"),
                "class_metrics": evaluation_manifest.get("classMetrics", {}),
                "samples_root_uri": evaluation_manifest.get("samplesRootUri"),
                "created_at": evaluation_manifest.get("createdAt"),
            },
        )
