from __future__ import annotations

from sceneops_core.ids.runs import default_inference_run_id
from sceneops_core.schemas.datasets import DatasetVersionStatus
from sceneops_core.schemas.jobs import (
    InferenceBackend,
    JobManifest,
    JobType,
    PredictDetectionJobParams,
    PredictDetectionJobResult,
)
from sceneops_worker.jobs.handlers.base import TypedJobHandler
from sceneops_worker.inference.mock_detection import generate_mock_predictions


class PredictDetectionJobHandler(
    TypedJobHandler[PredictDetectionJobParams, PredictDetectionJobResult]
):
    job_type = JobType.PREDICT_DETECTION

    def parse_params(self, job: JobManifest) -> PredictDetectionJobParams:
        return PredictDetectionJobParams.model_validate(job.params)

    async def run(
        self,
        *,
        params: PredictDetectionJobParams,
        job: JobManifest,
    ) -> PredictDetectionJobResult:
        if params.inference_backend == InferenceBackend.MOCK:
            return await self._run_mock_detection(params=params, job=job)

        raise ValueError(f"Unsupported inference backend: {params.inference_backend}")

    async def _run_mock_detection(
        self,
        *,
        params: PredictDetectionJobParams,
        job: JobManifest,
    ) -> PredictDetectionJobResult:
        version = await self.context.dataset_registry_store.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        if version.status not in {
            DatasetVersionStatus.INGESTED,
            # DatasetVersionStatus.READY,
        }:
            raise ValueError(
                f"Dataset version is not usable for prediction: "
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

        inference_run_id = params.inference_run_id or default_inference_run_id(
            job.job_id
        )

        run_manifest = await generate_mock_predictions(
            dataset_manifest=dataset_manifest,
            dataset_artifact_store=self.context.dataset_artifact_store,
            run_artifact_store=self.context.run_artifact_store,
            model_id=params.model_id,
            model_version=params.model_version,
            run_id=inference_run_id,
            max_samples=params.max_samples,
        )

        return PredictDetectionJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            inference_run_id=inference_run_id,
            prediction_manifest_uri=run_manifest["predictionManifestUri"],
            sample_count=int(run_manifest.get("sampleCount", 0)),
            result_summary={
                "prediction_count": run_manifest.get("predictionCount", 0),
                "status": run_manifest.get("status"),
                "predictions_root_uri": run_manifest.get("predictionsRootUri"),
                "created_at": run_manifest.get("createdAt"),
            },
        )
