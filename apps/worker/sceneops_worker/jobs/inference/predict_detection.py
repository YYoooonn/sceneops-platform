from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.derived_ids import (
    derived_artifact_id,
    prediction_manifest_artifact_id,
)
from sceneops_core.common.ids import default_inference_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
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
from sceneops_core.sample_views import SampleViewRef
from sceneops_core.scenarios import ScenarioSetRef
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.derived.resolution import (
    ResolvedScenarioSet,
    ResolvedView,
    resolve_sample_view,
    resolve_scenario_set,
)
from sceneops_worker.inference.detection import create_detection_inference_backend
from sceneops_worker.inference.detection.base import (
    DetectionInferenceRequest,
    SampleDetectionBackend,
)
from sceneops_worker.inference.detection.samples import (
    DetectionSampleSelection,
    load_detection_samples,
)
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.scenes.readiness import require_no_blocked_scenes


class DatasetScopeMismatchError(ValueError):
    """A pinned view belongs to a different DatasetVersion than the run."""


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
    """The pinned derived inputs of one run."""

    views: list[tuple[ResolvedView, list[str] | None]]
    scenario_set: ResolvedScenarioSet | None


@dataclass(frozen=True)
class PredictDetectionArtifacts:
    """Registered artifacts of one inference run."""

    prediction_manifest_uri: str
    prediction_manifest_checksum: str
    prediction_manifest_artifact_id: str
    predictions_root_uri: str | None


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
        backend = create_detection_inference_backend(params.inference_backend)
        selection = await load_detection_samples(
            context,
            views=inputs.views,
            camera_channel=params.camera_channel,
            lidar_channel=params.lidar_channel,
            max_samples=params.max_samples,
            with_reference_labels=isinstance(backend, SampleDetectionBackend)
            and backend.uses_reference_labels,
        )
        inference_result = await self._run_detection_inference(
            execution, inputs, selection, backend
        )
        artifacts = await self._register_artifacts(execution, inference_result)
        succeeded_record = self._build_succeeded_record(
            initial_record=initial_record,
            execution=execution,
            inference_result=inference_result,
            artifacts=artifacts,
            inputs=inputs,
            selection=selection,
        )
        job_result = self._build_result(
            execution=execution,
            inference_result=inference_result,
            artifacts=artifacts,
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

        dataset_version = await context.dataset_store.get_version(
            dataset_id=params.dataset_id, version=params.dataset_version
        )
        if dataset_version is None:
            raise ValueError(
                f"Dataset version not found: {params.dataset_id}:{params.dataset_version}"
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

    # ── input resolution ───────────────────────────────────────────────────────

    @staticmethod
    async def _resolve_inputs(
        execution: PredictDetectionExecution,
    ) -> PredictDetectionInputs:
        """Pinned views (from a ScenarioSet's members or the explicit list),
        each verified to belong to the run's DatasetVersion, and every Scene
        revision they pin checked against validation readiness."""
        context = execution.context
        params = execution.params

        scenario_set: ResolvedScenarioSet | None = None
        wanted: list[tuple[SampleViewRef, list[str] | None]]
        if params.scenario_set_id is not None:
            scenario_set = await resolve_scenario_set(context, params.scenario_set_id)
            wanted = [
                (member.sample_view, list(member.sample_ids))
                for member in scenario_set.manifest.members
            ]
        else:
            wanted = [(ref, None) for ref in params.sample_views]

        views: list[tuple[ResolvedView, list[str] | None]] = []
        for ref, sample_ids in wanted:
            resolved = await resolve_sample_view(context, ref)
            if (resolved.dataset_id, resolved.dataset_version) != (
                params.dataset_id,
                params.dataset_version,
            ):
                raise DatasetScopeMismatchError(
                    f"sample view {ref.manifest_artifact_id} belongs to "
                    f"{resolved.dataset_id}:{resolved.dataset_version}, not "
                    f"{params.dataset_id}:{params.dataset_version}"
                )
            views.append((resolved, sample_ids))

        await require_no_blocked_scenes(
            context, [resolved.view.scene for resolved, _ in views]
        )
        return PredictDetectionInputs(views=views, scenario_set=scenario_set)

    # ── inference ─────────────────────────────────────────────────────────────

    def _build_inference_config(
        self, execution: PredictDetectionExecution
    ) -> DetectionInferenceConfig:
        params = execution.params
        return DetectionInferenceConfig(
            model_id=params.model_id,
            model_version=params.model_version,
            inference_backend=params.inference_backend.value,
            camera_channel=params.camera_channel,
            lidar_channel=params.lidar_channel,
            detection_prompt=params.detection_prompt,
            box_threshold=params.box_threshold,
            text_threshold=params.text_threshold,
            max_image_size=params.max_image_size,
            max_samples=params.max_samples,
        )

    async def _run_detection_inference(
        self,
        execution: PredictDetectionExecution,
        inputs: PredictDetectionInputs,
        selection: DetectionSampleSelection,
        backend,
    ) -> DetectionInferenceResult:
        context = execution.context
        scenario_ref: ScenarioSetRef | None = (
            inputs.scenario_set.ref if inputs.scenario_set is not None else None
        )
        request = DetectionInferenceRequest(
            input=DetectionInferenceInput(
                run_id=execution.inference_run_id,
                config=self._build_inference_config(execution),
                dataset_id=execution.params.dataset_id,
                dataset_version=execution.params.dataset_version,
                inputs=selection.inputs,
                scenario_set=scenario_ref,
                model_uri=execution.model_uri,
                endpoint_url=execution.endpoint_url,
            ),
            samples=selection.samples,
            artifact_store=context.artifact_store,
            run_artifact_store=context.run_artifact_store,
            derived_store=context.derived_store,
        )
        return await backend.run(request)

    # ── artifact registration ──────────────────────────────────────────────────

    async def _register_artifacts(
        self,
        execution: PredictDetectionExecution,
        inference_result: DetectionInferenceResult,
    ) -> PredictDetectionArtifacts:
        context = execution.context
        job = execution.job
        params = execution.params
        run_id = execution.inference_run_id
        checksum = inference_result.prediction_manifest_checksum

        manifest_artifact_id = prediction_manifest_artifact_id(
            inference_run_id=run_id, checksum=checksum
        )
        await context.artifact_record_store.register(
            artifact_id=manifest_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.PREDICTION_MANIFEST,
                uri=inference_result.prediction_manifest_uri,
                media_type="application/json",
                checksum=checksum,
            ),
            owner_type=ArtifactOwnerType.INFERENCE_RUN,
            owner_id=run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )
        if inference_result.predictions_root_uri:
            await context.artifact_record_store.register(
                artifact_id=derived_artifact_id(
                    prefix="predictions",
                    logical_id=run_id,
                    checksum=checksum,
                ),
                ref=ArtifactRef(
                    kind=ArtifactKind.PREDICTIONS_ROOT,
                    uri=inference_result.predictions_root_uri,
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
            prediction_manifest_uri=inference_result.prediction_manifest_uri,
            prediction_manifest_checksum=checksum,
            prediction_manifest_artifact_id=manifest_artifact_id,
            predictions_root_uri=inference_result.predictions_root_uri,
        )

    # ── result/record assembly ─────────────────────────────────────────────────

    @staticmethod
    def _build_succeeded_record(
        *,
        initial_record: InferenceRunRecord,
        execution: PredictDetectionExecution,
        inference_result: DetectionInferenceResult,
        artifacts: PredictDetectionArtifacts,
        inputs: PredictDetectionInputs,
        selection: DetectionSampleSelection,
    ) -> InferenceRunRecord:
        pins: JsonDict = {
            "sample_views": [
                item.sample_view.model_dump(mode="json") for item in selection.inputs
            ],
            "skipped_samples": selection.skipped[:100],
        }
        if inputs.scenario_set is not None:
            pins["scenario_set"] = inputs.scenario_set.ref.model_dump(mode="json")
        return initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "sample_count": inference_result.sample_count,
                "prediction_count": inference_result.prediction_count,
                "prediction_manifest_uri": artifacts.prediction_manifest_uri,
                "prediction_manifest_checksum": artifacts.prediction_manifest_checksum,
                "predictions_root_uri": artifacts.predictions_root_uri,
                "metrics": inference_result.metrics,
                "metadata": {
                    "model_uri": execution.model_uri,
                    "endpoint_url": execution.endpoint_url,
                    "inputs": pins,
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
    ) -> PredictDetectionJobResult:
        params = execution.params
        return PredictDetectionJobResult(
            inference_run_id=execution.inference_run_id,
            prediction_manifest_uri=artifacts.prediction_manifest_uri,
            prediction_manifest_checksum=artifacts.prediction_manifest_checksum,
            prediction_manifest_artifact_id=artifacts.prediction_manifest_artifact_id,
            predictions_root_uri=artifacts.predictions_root_uri,
            model_id=params.model_id,
            model_version=params.model_version,
            inference_backend=params.inference_backend.value,
            scene_count=inference_result.scene_count,
            sample_count=inference_result.sample_count,
            inference_request_count=inference_result.inference_request_count,
            prediction_count=inference_result.prediction_count,
            evaluable_prediction_count=inference_result.evaluable_prediction_count,
            lifting_succeeded_count=inference_result.lifting_succeeded_count,
            lifting_failed_count=inference_result.lifting_failed_count,
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
