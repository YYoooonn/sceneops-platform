from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_inference_run_id, generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.inference.schemas import (
    DetectionInferenceConfig,
    DetectionInferenceInput,
)
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.jobs.schemas import (
    JobType,
    PredictDetectionJobParams,
    PredictDetectionJobResult,
)
from sceneops_core.models.schemas import ModelBackend
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.inference.detection import create_detection_inference_backend
from sceneops_worker.inference.detection.base import DetectionInferenceRequest
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
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
        job: Any,
        params: PredictDetectionJobParams,
        started_at: datetime,
    ) -> InferenceRunRecord:
        inference_run_id = params.inference_run_id or default_inference_run_id(
            job.job_id
        )
        return InferenceRunRecord(
            run_id=inference_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            inference_backend=params.inference_backend.value,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_step_run_id=job.pipeline_step_run_id,
            job_id=job.job_id,
            metadata={
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
        context: WorkerContext,
        initial_record: InferenceRunRecord,
        started_at: datetime,
    ) -> tuple[InferenceRunRecord, PredictDetectionJobResult]:
        inference_run_id = initial_record.run_id

        model_version = await context.model_store.get_version(
            model_id=params.model_id,
            version=params.model_version,
        )

        if model_version is None:
            raise ValueError(
                f"Model version not found: {params.model_id}:{params.model_version}"
            )

        _validate_model_backend(
            requested_backend=params.inference_backend,
            registered_backend=model_version.backend,
        )

        model_uri = params.model_uri or model_version.model_uri
        endpoint_url = params.endpoint_url or model_version.endpoint_url

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
                    run_id=inference_run_id,
                    config=DetectionInferenceConfig(
                        model_id=params.model_id,
                        model_version=params.model_version,
                        inference_backend=params.inference_backend.value,
                        max_samples=params.max_samples,
                        model_uri=model_uri,
                        endpoint_url=endpoint_url,
                    ),
                    dataset_manifest=dataset_manifest,
                ),
                dataset_artifact_store=context.dataset_artifact_store,
                run_artifact_store=context.run_artifact_store,
            )
        )

        prediction_manifest_uri = inference_result.run_manifest_uri
        predictions_root_uri = inference_result.predictions_root_uri

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.PREDICTION_MANIFEST,
                uri=prediction_manifest_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.INFERENCE_RUN,
            owner_id=inference_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=inference_run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        if predictions_root_uri:
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.PREDICTIONS_ROOT,
                    uri=predictions_root_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.INFERENCE_RUN,
                owner_id=inference_run_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                run_id=inference_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "sample_count": inference_result.sample_count,
                "prediction_count": inference_result.prediction_count,
                "prediction_manifest_uri": prediction_manifest_uri,
                "predictions_root_uri": predictions_root_uri,
                "metadata": {
                    "model_uri": model_uri,
                    "endpoint_url": endpoint_url,
                    "metrics": inference_result.metrics,
                    **inference_result.metadata,
                },
                "finished_at": utc_now(),
            }
        )

        job_result = PredictDetectionJobResult(
            inference_run_id=inference_run_id,
            prediction_manifest_uri=prediction_manifest_uri,
            predictions_root_uri=predictions_root_uri,
            model_id=params.model_id,
            model_version=params.model_version,
            inference_backend=params.inference_backend.value,
            sample_count=inference_result.sample_count,
            prediction_count=inference_result.prediction_count,
            metrics=inference_result.metrics,
            metadata={
                "model_uri": model_uri,
                "endpoint_url": endpoint_url,
            },
        )

        return succeeded_record, job_result

    async def _upsert(
        self, context: WorkerContext, record: InferenceRunRecord
    ) -> InferenceRunRecord:
        return await context.runs.inference.upsert(record)


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
