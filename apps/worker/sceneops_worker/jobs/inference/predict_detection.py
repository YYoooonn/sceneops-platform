from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.ids import default_inference_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.inference.schemas import DetectionInferenceInput
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.jobs.schemas import (
    PredictDetectionJobParams,
    PredictDetectionJobResult,
    JobType,
)
from sceneops_core.models.schemas import ModelBackend
from sceneops_core.runs.schemas import InferenceRunRecord, RunStatus
from sceneops_core.time import utc_now
from sceneops_worker.inference.detection import (
    create_detection_inference_backend,
)
from sceneops_worker.inference.detection.base import DetectionInferenceRequest
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest, RunRecordHandler
from sceneops_worker.jobs.context import JobContext
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


class PredictDetectionJobHandler(
    RunRecordHandler[
        PredictDetectionJobParams, PredictDetectionJobResult, InferenceRunRecord
    ],
    JobHandler[PredictDetectionJobParams, PredictDetectionJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.PREDICT_DETECTION

    @property
    def params_model(self) -> type[PredictDetectionJobParams]:
        return PredictDetectionJobParams

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        model_id = (
            base.get("model_id") or context_values.get("model_id") or "centerpoint-mock"
        )
        model_version = (
            base.get("model_version") or context_values.get("model_version") or "v0"
        )
        return {**base, "model_id": model_id, "model_version": model_version}

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = PredictDetectionJobResult.model_validate(result)
        return {
            Ctx.INFERENCE_RUN_ID: parsed.inference_run_id,
            Ctx.PREDICTION_MANIFEST_URI: parsed.prediction_manifest_uri,
            Ctx.PREDICTION_SAMPLE_COUNT: parsed.sample_count,
            Ctx.PREDICTION_MODEL_ID: parsed.model_id,
            Ctx.PREDICTION_MODEL_VERSION: parsed.model_version,
        }

    def build_initial_record(
        self,
        *,
        job: JobHandlerRequest | Any,
        params: PredictDetectionJobParams,
        started_at: datetime,
    ) -> InferenceRunRecord:
        inference_run_id = params.inference_run_id or default_inference_run_id(
            job.job_id
        )
        return InferenceRunRecord(
            id=inference_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_step_run_id=job.pipeline_step_run_id,
            job_id=job.job_id,
            metadata={
                "backend": params.inference_backend.value,
                "model_uri": params.model_uri,
                "endpoint_url": params.endpoint_url,
            },
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: PredictDetectionJobParams,
        context: JobContext,
        initial_record: InferenceRunRecord,
        started_at: datetime,
    ) -> tuple[InferenceRunRecord, PredictDetectionJobResult]:
        inference_run_id = initial_record.id

        model_version = await context.model_registry_store.get_version(
            model_id=params.model_id,
            model_version=params.model_version,
        )

        _validate_model_backend(
            requested_backend=params.inference_backend,
            registered_backend=model_version.backend,
        )

        model_uri = params.model_uri or model_version.model_uri
        endpoint_url = params.endpoint_url or model_version.endpoint_url

        version = await context.dataset_registry_store.get_version(
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

        dataset_manifest = await context.dataset_artifact_store.load_dataset_manifest(
            version.manifest_uri
        )

        backend = create_detection_inference_backend(params.inference_backend)

        inference_result = await backend.run(
            DetectionInferenceRequest(
                input=DetectionInferenceInput(
                    params=params,
                    dataset_manifest=dataset_manifest,
                    model_uri=model_uri,
                    endpoint_url=endpoint_url,
                    run_id=inference_run_id,
                ),
                dataset_artifact_store=context.dataset_artifact_store,
                run_artifact_store=context.run_artifact_store,
            )
        )

        metadata = {
            "backend": params.inference_backend.value,
            "model_uri": model_uri,
            "endpoint_url": endpoint_url,
            "metrics": inference_result.metrics,
            **inference_result.metadata,
        }

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "sample_count": inference_result.sample_count,
                "prediction_count": inference_result.prediction_count,
                "run_manifest_uri": inference_result.run_manifest_uri,
                "predictions_root_uri": inference_result.predictions_root_uri,
                "metadata": metadata,
                "finished_at": utc_now(),
            }
        )

        job_result = PredictDetectionJobResult(
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
                "backend": params.inference_backend.value,
                "metrics": inference_result.metrics,
            },
        )

        return succeeded_record, job_result

    async def _upsert(self, context: JobContext, record: InferenceRunRecord) -> None:
        await context.run_registry_store.upsert_inference_run(record)


def _validate_model_backend(
    *,
    requested_backend: InferenceBackendType,
    registered_backend: ModelBackend,
) -> None:
    if requested_backend.value != registered_backend.value:
        raise ValueError(
            f"Model backend mismatch: params={requested_backend.value}, "
            f"registry={registered_backend.value}"
        )
