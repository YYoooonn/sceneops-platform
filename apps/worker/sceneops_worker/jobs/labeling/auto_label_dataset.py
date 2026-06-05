from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.common.ids import default_auto_label_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.jobs.schemas import (
    AutoLabelDatasetJobParams,
    AutoLabelDatasetJobResult,
    JobType,
)
from sceneops_core.labels.schemas.runs import (
    DatasetAutoLabelRunRecord as AutoLabelRunRecord,
)
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.common.time import utc_now
from sceneops_worker.inference.detection import (
    create_detection_inference_backend,
)
from sceneops_worker.inference.detection.base import DetectionInferenceRequest
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx

# Re-use the DetectionInferenceInput schema since GroundingDinoDetectionBackend
# implements DetectionInferenceBackend and shares the same request/response types.
from sceneops_core.inference.schemas import DetectionInferenceInput


class AutoLabelDatasetJobHandler(
    RunRecordHandler[
        AutoLabelDatasetJobParams,
        AutoLabelDatasetJobResult,
        AutoLabelRunRecord,
    ],
    JobHandler[AutoLabelDatasetJobParams, AutoLabelDatasetJobResult],
):
    """Run GroundingDINO + frustum-LiDAR lifting on a dataset version.

    Reuses the DetectionInferenceBackend interface so predictions land in the
    same format as PREDICT_DETECTION, making them directly consumable by the
    existing EVALUATE_DETECTION pipeline step.

    Artifact layout (mirrors inference run layout):
      runs/auto_labels/<auto_label_run_id>/
        auto_label.json              ← run-level manifest
        samples/<sample_id>.json     ← per-sample predictions (same schema as inference)
    """

    @property
    def job_type(self) -> JobType:
        return JobType.AUTO_LABEL_DATASET

    @property
    def params_model(self) -> type[AutoLabelDatasetJobParams]:
        return AutoLabelDatasetJobParams

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        model_id = base.get("model_id") or context_values.get("model_id") or ""
        model_version = (
            base.get("model_version") or context_values.get("model_version") or "v0"
        )
        return {**base, "model_id": model_id, "model_version": model_version}

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = AutoLabelDatasetJobResult.model_validate(result)
        return {
            Ctx.AUTO_LABEL_RUN_ID: parsed.auto_label_run_id,
            Ctx.AUTO_LABEL_MANIFEST_URI: parsed.auto_label_manifest_uri,
            Ctx.AUTO_LABEL_SAMPLE_COUNT: parsed.sample_count,
            Ctx.AUTO_LABEL_LABELED_SAMPLE_COUNT: parsed.labeled_sample_count,
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: AutoLabelDatasetJobParams,
        started_at: datetime,
    ) -> AutoLabelRunRecord:
        auto_label_run_id = params.auto_label_run_id or default_auto_label_run_id(
            job.job_id
        )
        return AutoLabelRunRecord(
            id=auto_label_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            vlm_backend=params.vlm_backend,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_step_run_id=job.pipeline_step_run_id,
            job_id=job.job_id,
            metadata={
                "endpoint_url": params.endpoint_url,
                "vlm_backend": params.vlm_backend,
            },
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: AutoLabelDatasetJobParams,
        context: WorkerContext,
        initial_record: AutoLabelRunRecord,
        started_at: datetime,
    ) -> tuple[AutoLabelRunRecord, AutoLabelDatasetJobResult]:
        auto_label_run_id = initial_record.id

        version = await context.dataset_registry_store.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )
        if version.status not in (
            DatasetVersionStatus.READY,
            DatasetVersionStatus.INGESTED,
        ):
            raise ValueError(
                f"Dataset version must be ready or ingested for auto-labeling. "
                f"{params.dataset_id}:{params.dataset_version} status={version.status}"
            )
        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        dataset_manifest = await context.dataset_artifact_store.load_dataset_manifest(
            version.manifest_uri
        )

        # Resolve model endpoint_url — params take priority over registry.
        model_version = await context.model_registry_store.get_version(
            model_id=params.model_id,
            model_version=params.model_version,
        )
        endpoint_url = params.endpoint_url or model_version.endpoint_url

        _validate_backend(params.vlm_backend)

        backend = create_detection_inference_backend(params.vlm_backend)

        # Build a DetectionInferenceInput so GroundingDinoDetectionBackend can
        # resolve endpoint_url + dataset manifest without knowing it's auto-label.
        inference_input = DetectionInferenceInput(
            params=_coerce_to_predict_params(params),
            dataset_manifest=dataset_manifest,
            run_id=auto_label_run_id,
            model_uri=model_version.model_uri,
            endpoint_url=endpoint_url,
        )

        inference_result = await backend.run(
            DetectionInferenceRequest(
                input=inference_input,
                dataset_artifact_store=context.dataset_artifact_store,
                run_artifact_store=context.run_artifact_store,
            )
        )

        # Write auto-label run manifest re-using the inference run manifest
        # that GroundingDinoDetectionBackend already wrote under runs/inference/.
        # We additionally record it under runs/auto_labels/ for clear provenance.
        auto_label_manifest = {
            "auto_label_run_id": auto_label_run_id,
            "inference_run_id": auto_label_run_id,
            "dataset_id": params.dataset_id,
            "dataset_version": params.dataset_version,
            "model_id": params.model_id,
            "model_version": params.model_version,
            "vlm_backend": params.vlm_backend,
            "endpoint_url": endpoint_url,
            "status": "succeeded",
            "sample_count": inference_result.sample_count,
            "labeled_sample_count": inference_result.prediction_count,
            "predictions_root_uri": inference_result.predictions_root_uri,
            "metrics": inference_result.metrics,
            "created_at": utc_now().isoformat(),
        }
        auto_label_manifest_uri = (
            await context.run_artifact_store.write_auto_label_run_manifest(
                auto_label_run_id=auto_label_run_id,
                manifest=auto_label_manifest,
            )
        )

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "sample_count": inference_result.sample_count,
                "labeled_sample_count": inference_result.prediction_count,
                "auto_label_manifest_uri": auto_label_manifest_uri,
                "samples_root_uri": inference_result.predictions_root_uri,
                "metrics": inference_result.metrics,
                "finished_at": utc_now(),
            }
        )

        job_result = AutoLabelDatasetJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            auto_label_run_id=auto_label_run_id,
            auto_label_manifest_uri=auto_label_manifest_uri,
            sample_count=inference_result.sample_count,
            labeled_sample_count=inference_result.prediction_count,
            metrics=inference_result.metrics,
        )

        return succeeded_record, job_result

    async def _upsert(self, context: WorkerContext, record: AutoLabelRunRecord) -> None:
        await context.run_registry_store.upsert_auto_label_run(record)


def _validate_backend(backend: InferenceBackendType) -> None:
    if backend != InferenceBackendType.GROUNDING_DINO:
        raise ValueError(
            f"AUTO_LABEL_DATASET currently supports only the grounding_dino backend. "
            f"Got: {backend}. "
            f"To add a new backend, implement DetectionInferenceBackend and register it."
        )


def _coerce_to_predict_params(
    params: AutoLabelDatasetJobParams,
) -> Any:
    """Wrap AutoLabelDatasetJobParams into a duck-typed object that satisfies
    DetectionInferenceInput.params (which expects PredictDetectionJobParams fields).

    GroundingDinoDetectionBackend reads: dataset_id, dataset_version,
    max_samples, model_id, model_version, inference_backend.
    We supply those from the auto-label params without adding a hard dependency
    on PredictDetectionJobParams in this handler.
    """
    from sceneops_core.jobs.schemas import PredictDetectionJobParams

    return PredictDetectionJobParams(
        dataset_id=params.dataset_id,
        dataset_version=params.dataset_version,
        model_id=params.model_id,
        model_version=params.model_version,
        inference_backend=InferenceBackendType.GROUNDING_DINO,
        max_samples=params.max_samples,
        endpoint_url=params.endpoint_url,
    )
