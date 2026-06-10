from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_inference_run_id, generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetManifest, DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.inference.schemas import (
    DetectionInferenceConfig,
    DetectionInferenceInput,
)
from sceneops_core.inference.schemas.detection import DetectionInferenceResult
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.jobs.schemas import (
    JobManifest,
    JobType,
    PredictDetectionJobParams,
    PredictDetectionJobResult,
)
from sceneops_core.models.schemas import ModelBackend
from sceneops_core.models.schemas.records import ModelVersionRecord
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.inference.detection import create_detection_inference_backend
from sceneops_worker.inference.detection.base import DetectionInferenceRequest
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler


@dataclass(frozen=True)
class PredictDetectionExecution:
    """Resolved execution context for one predict_detection job invocation."""

    job: JobManifest
    params: PredictDetectionJobParams
    context: WorkerContext
    inference_run_id: str
    dataset_version_record: DatasetVersionRecord
    model_version_record: ModelVersionRecord
    model_uri: str | None
    endpoint_url: str | None


@dataclass(frozen=True)
class PredictDetectionInputs:
    """Resolved dataset manifest."""

    dataset_manifest: DatasetManifest
    dataset_manifest_uri: str


@dataclass(frozen=True)
class PredictDetectionArtifacts:
    """Registered artifact URIs for one inference run."""

    prediction_manifest_uri: str
    predictions_root_uri: str | None


@dataclass(frozen=True)
class PredictionCounts:
    """Aggregated counts extracted from a DetectionInferenceResult."""

    scene_count: int
    sample_count: int
    inference_request_count: int
    prediction_count: int
    evaluable_prediction_count: int
    lifting_succeeded_count: int
    lifting_failed_count: int


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

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        model = inputs.model
        resolved_model_id = inputs.params.get("model_id") or (
            model.model_id if model else None
        )
        resolved_model_version = inputs.params.get("model_version") or (
            model.model_version if model else None
        )
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            # Override after spread so resolved values always win.
            "model_id": resolved_model_id,
            "model_version": resolved_model_version,
        }

    def build_initial_record(
        self,
        *,
        job: JobManifest,
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
            pipeline_task_run_id=job.pipeline_task_run_id,
            job_id=job.job_id,
            # metadata={
            #     "model_uri": params.model_uri,
            #     "endpoint_url": params.endpoint_url,
            # },
            started_at=started_at,
        )

    # ── orchestration ──────────────────────────────────────────────────────────

    async def execute(
        self,
        *,
        job: JobManifest,
        params: PredictDetectionJobParams,
        context: WorkerContext,
        initial_record: InferenceRunRecord,
        started_at: datetime,
    ) -> tuple[InferenceRunRecord, PredictDetectionJobResult]:
        execution = await self._prepare_execution(
            job=job, params=params, context=context, initial_record=initial_record
        )
        inputs = await self._resolve_inputs(execution)
        inference_result = await self._run_detection_inference(execution, inputs)
        artifacts = await self._register_artifacts(execution, inference_result)
        counts = self._extract_prediction_counts(inference_result)
        succeeded_record = self._build_succeeded_record(
            initial_record=initial_record,
            execution=execution,
            inference_result=inference_result,
            artifacts=artifacts,
        )
        job_result = self._build_result(
            execution=execution,
            inference_result=inference_result,
            artifacts=artifacts,
            counts=counts,
        )
        return succeeded_record, job_result

    # ── execution resolution ───────────────────────────────────────────────────

    async def _prepare_execution(
        self,
        *,
        job: JobManifest,
        params: PredictDetectionJobParams,
        context: WorkerContext,
        initial_record: InferenceRunRecord,
    ) -> PredictDetectionExecution:
        model_version = await self._require_model_version(
            context, params.model_id, params.model_version
        )
        self._validate_model_backend(params.inference_backend, model_version.backend)

        model_uri = model_version.model_uri
        endpoint_url = model_version.endpoint_url

        dataset_version = await self._require_ready_dataset_version(
            context, params.dataset_id, params.dataset_version
        )
        self._validate_backend_inputs(
            params.inference_backend,
            model_uri=model_uri,
            endpoint_url=endpoint_url,
        )

        return PredictDetectionExecution(
            job=job,
            params=params,
            context=context,
            inference_run_id=initial_record.run_id,
            dataset_version_record=dataset_version,
            model_version_record=model_version,
            model_uri=model_uri,
            endpoint_url=endpoint_url,
        )

    @staticmethod
    async def _require_ready_dataset_version(
        context: WorkerContext,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetVersionRecord:
        version = await context.dataset_store.get_version(
            dataset_id=dataset_id, version=dataset_version
        )
        if version is None:
            raise ValueError(
                f"Dataset version not found: {dataset_id}:{dataset_version}"
            )
        if version.status != DatasetVersionStatus.READY:
            raise ValueError(
                f"Dataset version is not usable for prediction: "
                f"{dataset_id}:{dataset_version}, status={version.status}"
            )
        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: {dataset_id}:{dataset_version}"
            )
        return version

    @staticmethod
    async def _require_model_version(
        context: WorkerContext,
        model_id: str,
        model_version_str: str,
    ) -> ModelVersionRecord:
        mv = await context.model_store.get_version(
            model_id=model_id, version=model_version_str
        )
        if mv is None:
            raise ValueError(f"Model version not found: {model_id}:{model_version_str}")
        return mv

    @staticmethod
    def _validate_model_backend(
        requested: InferenceBackendType,
        registered: ModelBackend,
    ) -> None:
        if requested.value != registered.value:
            raise ValueError(
                f"Model backend mismatch: "
                f"params={requested.value}, registry={registered.value}"
            )

    @staticmethod
    def _validate_backend_inputs(
        backend: InferenceBackendType,
        *,
        model_uri: str | None,
        endpoint_url: str | None,
    ) -> None:
        if backend == InferenceBackendType.GROUNDING_DINO:
            if not endpoint_url:
                raise ValueError(
                    "GroundingDINO backend requires endpoint_url "
                    "(e.g. http://sceneops-inference:8001)"
                )
        if backend == InferenceBackendType.ONNX_RUNTIME and not model_uri:
            raise ValueError("ONNX Runtime backend requires model_uri")

    # ── input resolution ───────────────────────────────────────────────────────

    @staticmethod
    async def _resolve_inputs(
        execution: PredictDetectionExecution,
    ) -> PredictDetectionInputs:
        version = execution.dataset_version_record
        dataset_manifest = (
            await execution.context.dataset_artifact_store.load_dataset_manifest(
                version.manifest_uri
            )
        )
        return PredictDetectionInputs(
            dataset_manifest=dataset_manifest,
            dataset_manifest_uri=version.manifest_uri,
        )

    # ── inference ─────────────────────────────────────────────────────────────

    async def _run_detection_inference(
        self,
        execution: PredictDetectionExecution,
        inputs: PredictDetectionInputs,
    ) -> DetectionInferenceResult:
        backend = create_detection_inference_backend(execution.params.inference_backend)
        request = self._build_inference_request(execution, inputs)
        return await backend.run(request)

    def _build_inference_config(
        self, execution: PredictDetectionExecution
    ) -> DetectionInferenceConfig:
        params = execution.params
        return DetectionInferenceConfig(
            model_id=params.model_id,
            model_version=params.model_version,
            inference_backend=params.inference_backend.value,
            model_uri=execution.model_uri,
            endpoint_url=execution.endpoint_url,
            raw_source_root_uri=execution.dataset_version_record.raw_source_root_uri,
            scene_ids=params.scene_ids,
            max_scenes=params.max_scenes,
            max_samples=params.max_samples,
            camera_channel=params.camera_channel,
            detection_prompt=params.detection_prompt,
            box_threshold=params.box_threshold,
            text_threshold=params.text_threshold,
            max_image_size=params.max_image_size,
            enable_3d_lifting=params.enable_3d_lifting,
        )

    def _build_inference_request(
        self,
        execution: PredictDetectionExecution,
        inputs: PredictDetectionInputs,
    ) -> DetectionInferenceRequest:
        return DetectionInferenceRequest(
            input=DetectionInferenceInput(
                run_id=execution.inference_run_id,
                config=self._build_inference_config(execution),
                dataset_manifest=inputs.dataset_manifest,
            ),
            scene_artifact_store=execution.context.scene_artifact_store,
            run_artifact_store=execution.context.run_artifact_store,
        )

    # ── artifact registration ──────────────────────────────────────────────────

    async def _register_artifacts(
        self,
        execution: PredictDetectionExecution,
        inference_result: DetectionInferenceResult,
    ) -> PredictDetectionArtifacts:
        prediction_manifest_uri = inference_result.prediction_manifest_uri
        predictions_root_uri = inference_result.predictions_root_uri

        context = execution.context
        job = execution.job
        params = execution.params
        run_id = execution.inference_run_id

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.PREDICTION_MANIFEST,
                uri=prediction_manifest_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.INFERENCE_RUN,
            owner_id=run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=run_id,
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
                owner_id=run_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                run_id=run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        return PredictDetectionArtifacts(
            prediction_manifest_uri=prediction_manifest_uri,
            predictions_root_uri=predictions_root_uri,
        )

    # ── result/record assembly ─────────────────────────────────────────────────

    @staticmethod
    def _extract_prediction_counts(
        inference_result: DetectionInferenceResult,
    ) -> PredictionCounts:
        return PredictionCounts(
            scene_count=inference_result.scene_count,
            sample_count=inference_result.sample_count,
            inference_request_count=inference_result.inference_request_count,
            prediction_count=inference_result.prediction_count,
            evaluable_prediction_count=inference_result.evaluable_prediction_count,
            lifting_succeeded_count=inference_result.lifting_succeeded_count,
            lifting_failed_count=inference_result.lifting_failed_count,
        )

    @staticmethod
    def _build_succeeded_record(
        *,
        initial_record: InferenceRunRecord,
        execution: PredictDetectionExecution,
        inference_result: DetectionInferenceResult,
        artifacts: PredictDetectionArtifacts,
    ) -> InferenceRunRecord:
        return initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "sample_count": inference_result.sample_count,
                "prediction_count": inference_result.prediction_count,
                "prediction_manifest_uri": artifacts.prediction_manifest_uri,
                "predictions_root_uri": artifacts.predictions_root_uri,
                "metrics": inference_result.metrics,
                "metadata": {
                    "model_uri": execution.model_uri,
                    "endpoint_url": execution.endpoint_url,
                    **inference_result.metadata,
                },
                "finished_at": utc_now(),
            }
        )

    @staticmethod
    def _build_result(
        *,
        execution: PredictDetectionExecution,
        inference_result: DetectionInferenceResult,
        artifacts: PredictDetectionArtifacts,
        counts: PredictionCounts,
    ) -> PredictDetectionJobResult:
        params = execution.params
        return PredictDetectionJobResult(
            inference_run_id=execution.inference_run_id,
            prediction_manifest_uri=artifacts.prediction_manifest_uri,
            predictions_root_uri=artifacts.predictions_root_uri,
            model_id=params.model_id,
            model_version=params.model_version,
            inference_backend=params.inference_backend.value,
            scene_count=counts.scene_count,
            sample_count=counts.sample_count,
            inference_request_count=counts.inference_request_count,
            prediction_count=counts.prediction_count,
            evaluable_prediction_count=counts.evaluable_prediction_count,
            lifting_succeeded_count=counts.lifting_succeeded_count,
            lifting_failed_count=counts.lifting_failed_count,
            metrics=inference_result.metrics,
            metadata={
                "model_uri": execution.model_uri,
                "endpoint_url": execution.endpoint_url,
            },
        )

    # ── run record upsert ─────────────────────────────────────────────────────

    async def _upsert(
        self, context: WorkerContext, record: InferenceRunRecord
    ) -> InferenceRunRecord:
        return await context.runs.inference.upsert(record)
