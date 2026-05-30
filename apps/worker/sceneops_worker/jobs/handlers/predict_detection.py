from __future__ import annotations

from sceneops_core.ids.runs import default_inference_run_id
from sceneops_core.schemas.datasets import DatasetVersionStatus
from sceneops_core.schemas.inference import DetectionInferenceInput
from sceneops_core.schemas.jobs import (
    InferenceBackend,
    JobManifest,
    JobType,
    PredictDetectionJobParams,
    PredictDetectionJobResult,
)
from sceneops_core.schemas.models import ModelBackend
from sceneops_core.schemas.runs import InferenceRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.inference.detection import (
    create_detection_inference_backend,
)
from sceneops_worker.inference.detection.base import DetectionInferenceRequest
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

        inference_backend = params.inference_backend

        _validate_model_backend(
            requested_backend=inference_backend,
            registered_backend=model_version.backend,
        )

        model_uri = params.model_uri or model_version.model_uri
        endpoint_url = params.endpoint_url or model_version.endpoint_url

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

        metadata = {
            "backend": inference_backend.value,
            "model_uri": model_uri,
            "endpoint_url": endpoint_url,
        }

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
                metadata=metadata,
            )
        )

        try:
            backend = create_detection_inference_backend(inference_backend)

            inference_result = await backend.run(
                DetectionInferenceRequest(
                    input=DetectionInferenceInput(
                        params=params,
                        dataset_manifest=dataset_manifest,
                        model_uri=model_uri,
                        endpoint_url=endpoint_url,
                        run_id=inference_run_id,
                    ),
                    dataset_artifact_store=self.context.dataset_artifact_store,
                    run_artifact_store=self.context.run_artifact_store,
                )
            )

            await self.context.run_registry_store.upsert_inference_run(
                InferenceRunRecord(
                    id=inference_run_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    model_id=params.model_id,
                    model_version=params.model_version,
                    status=RunStatus.SUCCEEDED,
                    sample_count=inference_result.sample_count,
                    prediction_count=inference_result.prediction_count,
                    run_manifest_uri=inference_result.run_manifest_uri,
                    predictions_root_uri=inference_result.predictions_root_uri,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_step_run_id=job.pipeline_step_run_id,
                    job_id=job.job_id,
                    metadata={
                        **metadata,
                        "metrics": inference_result.metrics,
                        **inference_result.metadata,
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
                prediction_manifest_uri=inference_result.run_manifest_uri,
                sample_count=inference_result.sample_count,
                result_summary={
                    "prediction_count": inference_result.prediction_count,
                    "status": inference_result.status,
                    "predictions_root_uri": inference_result.predictions_root_uri,
                    "backend": inference_backend.value,
                    "metrics": inference_result.metrics,
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
                    metadata=metadata,
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


def _validate_model_backend(
    *,
    requested_backend: InferenceBackend,
    registered_backend: ModelBackend,
) -> None:
    if requested_backend.value != registered_backend.value:
        raise ValueError(
            f"Model backend mismatch: params={requested_backend.value}, "
            f"registry={registered_backend.value}"
        )
