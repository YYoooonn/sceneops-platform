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
from sceneops_core.schemas.models import ModelBackend, ModelVersionStatus
from sceneops_core.schemas.runs import InferenceRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.inference.mock_detection import generate_mock_predictions
from sceneops_worker.jobs.handlers.base import TypedJobHandler


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
        model_version = await self.context.model_registry_store.get_version(
            model_id=params.model_id,
            model_version=params.model_version,
        )

        if model_version.status != ModelVersionStatus.READY:
            raise ValueError(
                f"Model version is not ready: "
                f"{params.model_id}:{params.model_version}, "
                f"status={model_version.status}"
            )

        inference_backend = params.inference_backend

        if inference_backend == InferenceBackend.MOCK:
            if model_version.backend != ModelBackend.MOCK:
                raise ValueError(
                    f"Model backend mismatch: params={inference_backend}, "
                    f"registry={model_version.backend}"
                )

            return await self._run_mock_detection(
                params=params,
                job=job,
                model_uri=model_version.model_uri,
                endpoint_url=model_version.endpoint_url,
            )

        raise ValueError(f"Unsupported inference backend: {inference_backend}")

    async def _run_mock_detection(
        self,
        *,
        params: PredictDetectionJobParams,
        job: JobManifest,
        model_uri: str | None,
        endpoint_url: str | None,
    ) -> PredictDetectionJobResult:
        version = await self.context.dataset_registry_store.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        if version.status != DatasetVersionStatus.READY:
            raise ValueError(
                f"Dataset version is not usable for prediction: "
                f"{params.dataset_id}:{params.dataset_version}, "
                f"status={version.status}"
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

        started_at = utc_now()

        await self.context.run_registry_store.upsert_inference_run(
            InferenceRunRecord(
                id=inference_run_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                model_id=params.model_id,
                model_version=params.model_version,
                status=RunStatus.RUNNING,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_step_run_id=job.pipeline_step_run_id,
                job_id=job.job_id,
                started_at=started_at,
                metadata={
                    "backend": params.inference_backend.value,
                    "model_uri": params.model_uri or model_uri,
                    "endpoint_url": params.endpoint_url or endpoint_url,
                },
            )
        )

        try:
            run_manifest = await generate_mock_predictions(
                dataset_manifest=dataset_manifest,
                dataset_artifact_store=self.context.dataset_artifact_store,
                run_artifact_store=self.context.run_artifact_store,
                model_id=params.model_id,
                model_version=params.model_version,
                run_id=inference_run_id,
                max_samples=params.max_samples,
            )

            run_manifest_uri = run_manifest["predictionManifestUri"]
            predictions_root_uri = run_manifest["predictionsRootUri"]
            sample_count = int(run_manifest.get("sampleCount", 0))
            prediction_count = int(run_manifest.get("predictionCount", 0))

            await self.context.run_registry_store.upsert_inference_run(
                InferenceRunRecord(
                    id=inference_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    model_id=params.model_id,
                    model_version=params.model_version,
                    status=RunStatus.SUCCEEDED,
                    sample_count=sample_count,
                    prediction_count=prediction_count,
                    run_manifest_uri=run_manifest_uri,
                    predictions_root_uri=predictions_root_uri,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    metadata={
                        "backend": params.inference_backend.value,
                        "model_uri": params.model_uri or model_uri,
                        "endpoint_url": params.endpoint_url or endpoint_url,
                    },
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )

            return PredictDetectionJobResult(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                model_id=params.model_id,
                model_version=params.model_version,
                inference_run_id=inference_run_id,
                prediction_manifest_uri=run_manifest_uri,
                sample_count=sample_count,
                result_summary={
                    "prediction_count": prediction_count,
                    "status": run_manifest.get("status"),
                    "predictions_root_uri": predictions_root_uri,
                    "created_at": run_manifest.get("createdAt"),
                },
            )

        except Exception as error:
            await self.context.run_registry_store.upsert_inference_run(
                InferenceRunRecord(
                    id=inference_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    model_id=params.model_id,
                    model_version=params.model_version,
                    status=RunStatus.FAILED,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    metadata={
                        "backend": params.inference_backend.value,
                        "model_uri": params.model_uri or model_uri,
                        "endpoint_url": params.endpoint_url or endpoint_url,
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
