from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildScenesJobParams,
    BuildScenesJobResult,
    JobManifest,
    JobType,
)
from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawLogSourceType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.observations.artifacts import ObservationArtifactStore
from sceneops_worker.observations.adapters.factory import RawLogAdapterFactory
from sceneops_worker.scenes.raw_scene_builder import RawSceneBuilder, SceneBuildResult


@dataclass(frozen=True)
class BuildScenesExecution:
    """Resolved inputs and collaborators for one build_scenes handler invocation."""

    job: JobManifest
    params: BuildScenesJobParams
    context: WorkerContext
    raw_log_id: str
    obs_store: ObservationArtifactStore
    dataset_version_record: DatasetVersionRecord
    version_root_uri: str


@dataclass(frozen=True)
class BuildScenesRawInputs:
    """Resolved raw log manifest + frame index with their storage URIs."""

    raw_manifest: RawLogManifest
    frame_index: RawLogFrameIndex
    raw_manifest_uri: str
    raw_frame_index_uri: str


class BuildScenesJobHandler(JobHandler[BuildScenesJobParams, BuildScenesJobResult]):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SCENES

    @property
    def params_model(self) -> type[BuildScenesJobParams]:
        return BuildScenesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        params: JsonDict = {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }
        # Inject dataset required_channels into sampling unless already overridden.
        dataset_channels = inputs.dataset.required_channels if inputs.dataset else []
        if dataset_channels:
            sampling = dict(params.get("sampling") or {})
            if not sampling.get("required_channels"):
                sampling["required_channels"] = dataset_channels
                params["sampling"] = sampling
        return params

    # ── orchestration ──────────────────────────────────────────────────────────

    async def run(
        self,
        request: JobHandlerRequest[BuildScenesJobParams],
    ) -> BuildScenesJobResult:
        params = request.params
        context = request.context

        dataset_id = params.dataset_id or context.default_dataset_id
        dataset_version = params.dataset_version or context.default_dataset_version
        version_record = await self._require_version_with_source(
            context, dataset_id, dataset_version
        )

        execution = self._prepare_execution(request, version_record=version_record)

        version = await self._mark_dataset_version_ingesting(execution)

        raw_inputs = await self._resolve_raw_log_inputs(execution)

        scene_build_result = await self._build_raw_scenes(
            execution=execution,
            raw_inputs=raw_inputs,
        )

        await self._register_scene_artifacts(
            execution=execution,
            scene_build_result=scene_build_result,
        )

        await self._mark_dataset_version_ingested(
            execution=execution,
            version=version,
            raw_inputs=raw_inputs,
            scene_build_result=scene_build_result,
        )

        return self._build_result(
            execution=execution,
            raw_inputs=raw_inputs,
            scene_build_result=scene_build_result,
        )

    # ── version validation ─────────────────────────────────────────────────────

    @staticmethod
    async def _require_version_with_source(
        context: WorkerContext,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetVersionRecord:
        """Load and validate a registered DatasetVersionRecord"""
        version = await context.dataset_store.get_version(
            dataset_id=dataset_id, version=dataset_version
        )
        if version is None:
            raise ValueError(
                f"Dataset version not registered: {dataset_id}/{dataset_version}"
            )
        if not version.raw_source_root_uri:
            raise ValueError(
                f"Dataset version has no raw source root URI: "
                f"{dataset_id}/{dataset_version}"
            )
        return version

    # ── setup ──────────────────────────────────────────────────────────────────

    def _prepare_execution(
        self,
        request: JobHandlerRequest[BuildScenesJobParams],
        *,
        version_record: DatasetVersionRecord,
    ) -> BuildScenesExecution:
        job = request.job
        params = request.params
        context = request.context

        obs_store = self._build_observation_store(context)
        raw_log_id = (
            params.raw_log_id or f"{version_record.dataset_id}-{version_record.version}"
        )
        version_root_uri = self._resolve_version_root_uri(
            context,
            version_record.dataset_id,
            version_record.version,
        )

        return BuildScenesExecution(
            job=job,
            params=params,
            context=context,
            raw_log_id=raw_log_id,
            obs_store=obs_store,
            dataset_version_record=version_record,
            version_root_uri=version_root_uri,
        )

    @staticmethod
    def _build_observation_store(context: WorkerContext) -> ObservationArtifactStore:
        return ObservationArtifactStore(
            artifact_store=context.artifact_store,
            dataset_root_uri=context.settings.dataset_root_uri,
        )

    @staticmethod
    def _resolve_version_root_uri(
        context: WorkerContext,
        dataset_id: str,
        dataset_version: str,
    ) -> str:
        return context.dataset_artifact_store.dataset_version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )

    # ── dataset version lifecycle ──────────────────────────────────────────────

    async def _mark_dataset_version_ingesting(
        self, execution: BuildScenesExecution
    ) -> DatasetVersionRecord:
        return await execution.context.dataset_store.save_version(
            execution.dataset_version_record.model_copy(
                update={"status": DatasetVersionStatus.INGESTING}
            )
        )

    async def _mark_dataset_version_ingested(
        self,
        *,
        execution: BuildScenesExecution,
        version: DatasetVersionRecord,
        raw_inputs: BuildScenesRawInputs,
        scene_build_result: SceneBuildResult,
    ) -> None:
        channels = sorted(raw_inputs.raw_manifest.channels)
        await execution.context.dataset_store.save_version(
            version.model_copy(
                update={
                    "status": DatasetVersionStatus.INGESTED,
                    "scene_count": len(scene_build_result.scene_ids),
                    "sample_count": scene_build_result.total_samples,
                    "frame_count": scene_build_result.total_frames,
                    "channels": channels,
                }
            )
        )

    # ── raw log resolution ─────────────────────────────────────────────────────

    async def _resolve_raw_log_inputs(
        self, execution: BuildScenesExecution
    ) -> BuildScenesRawInputs:
        params = execution.params
        if params.raw_log_manifest_uri and params.raw_log_frame_index_uri:
            raw_manifest, frame_index = await self._load_raw_artifacts(
                obs_store=execution.obs_store,
                manifest_uri=params.raw_log_manifest_uri,
                frame_index_uri=params.raw_log_frame_index_uri,
            )
            return BuildScenesRawInputs(
                raw_manifest=raw_manifest,
                frame_index=frame_index,
                raw_manifest_uri=params.raw_log_manifest_uri,
                raw_frame_index_uri=params.raw_log_frame_index_uri,
            )
        return await self._build_raw_log_with_adapter(execution)

    @staticmethod
    async def _load_raw_artifacts(
        *,
        obs_store: ObservationArtifactStore,
        manifest_uri: str,
        frame_index_uri: str,
    ) -> tuple[RawLogManifest, RawLogFrameIndex]:
        raw = await obs_store.artifact_store.read_json(manifest_uri)
        manifest = RawLogManifest.model_validate(raw)

        raw_fi = await obs_store.artifact_store.read_json(frame_index_uri)
        frame_index = RawLogFrameIndex.model_validate(raw_fi)

        return manifest, frame_index

    async def _build_raw_log_with_adapter(
        self, execution: BuildScenesExecution
    ) -> BuildScenesRawInputs:
        params = execution.params
        version_record = execution.dataset_version_record
        adapter_factory = self._build_adapter_factory(
            execution=execution,
            obs_store=execution.obs_store,
        )
        source_type = params.source_type or RawLogSourceType.NUSCENES_RAW_LOG_MOCK
        adapter = adapter_factory.get(source_type)

        (
            raw_manifest,
            frame_index,
            raw_manifest_uri,
            raw_frame_index_uri,
        ) = await adapter.build_raw_log(
            dataset_id=version_record.dataset_id,
            dataset_version=version_record.version,
            raw_log_id=execution.raw_log_id,
            version_root_uri=execution.version_root_uri,
            params=params.model_dump(mode="python"),
        )

        return BuildScenesRawInputs(
            raw_manifest=raw_manifest,
            frame_index=frame_index,
            raw_manifest_uri=raw_manifest_uri,
            raw_frame_index_uri=raw_frame_index_uri,
        )

    @staticmethod
    def _build_adapter_factory(
        *,
        execution: BuildScenesExecution,
        obs_store: ObservationArtifactStore,
    ) -> RawLogAdapterFactory:
        params = execution.params
        version_record = execution.dataset_version_record

        # pylint: disable=import-outside-toplevel
        from sceneops_worker.datasets.ingestion.nuscenes_raw_log import (
            NuScenesRawLogMocker,
        )

        factory = RawLogAdapterFactory()

        required_channels = (
            set(params.sampling.required_channels)
            if params.sampling.required_channels
            else None
        )

        factory.register(
            RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
            NuScenesRawLogMocker(
                source_store=execution.context.raw_source_store,
                source_root_uri=version_record.raw_source_root_uri,
                observation_store=obs_store,
                required_channels=required_channels,
            ),
        )

        return factory

    # ── scene building ─────────────────────────────────────────────────────────

    async def _build_raw_scenes(
        self,
        *,
        execution: BuildScenesExecution,
        raw_inputs: BuildScenesRawInputs,
    ) -> SceneBuildResult:
        version_record = execution.dataset_version_record
        builder = RawSceneBuilder(
            scene_artifact_store=execution.context.scene_artifact_store,
            observation_artifact_store=execution.obs_store,
        )
        return await builder.build(
            manifest=raw_inputs.raw_manifest,
            frame_index=raw_inputs.frame_index,
            dataset_id=version_record.dataset_id,
            dataset_version=version_record.version,
            version_root_uri=execution.version_root_uri,
            segmentation=execution.params.segmentation,
            sampling=execution.params.sampling,
            max_built_scenes=execution.params.max_built_scenes,
        )

    # ── artifact registration ──────────────────────────────────────────────────

    async def _register_scene_artifacts(
        self,
        *,
        execution: BuildScenesExecution,
        scene_build_result: SceneBuildResult,
    ) -> None:
        context = execution.context
        version_record = execution.dataset_version_record
        for scene_id, uri in zip(
            scene_build_result.scene_ids, scene_build_result.scene_manifest_uris
        ):
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.SCENE_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE,
                owner_id=scene_id,
                dataset_id=version_record.dataset_id,
                dataset_version=version_record.version,
                scene_id=scene_id,
                job_id=execution.job.job_id,
                pipeline_run_id=execution.job.pipeline_run_id,
            )

    # ── result assembly ────────────────────────────────────────────────────────

    def _build_result(
        self,
        *,
        execution: BuildScenesExecution,
        raw_inputs: BuildScenesRawInputs,
        scene_build_result: SceneBuildResult,
    ) -> BuildScenesJobResult:
        channels = sorted(raw_inputs.raw_manifest.channels)
        report = scene_build_result.grouping_report

        return BuildScenesJobResult(
            raw_log_id=execution.raw_log_id,
            scene_ids=scene_build_result.scene_ids,
            scene_manifest_uris=scene_build_result.scene_manifest_uris,
            scene_count=len(scene_build_result.scene_ids),
            sample_count=scene_build_result.total_samples,
            frame_count=scene_build_result.total_frames,
            scene_segment_index_uri=scene_build_result.segment_index_uri,
            raw_log_manifest_uri=raw_inputs.raw_manifest_uri,
            raw_log_frame_index_uri=raw_inputs.raw_frame_index_uri,
            source_type=str(raw_inputs.raw_manifest.source_type)
            if raw_inputs.raw_manifest.source_type
            else None,
            source_format=str(raw_inputs.raw_manifest.source_format),
            observation_count=scene_build_result.observation_count,
            channels=channels,
            segmentation_strategy=str(execution.params.segmentation.strategy),
            sampling_strategy=str(execution.params.sampling.strategy),
            sample_count_before_filtering=report.sample_count_before_filtering,
            sample_count_after_filtering=report.sample_count_after_filtering,
            dropped_sample_count=report.dropped_sample_count,
            warned_sample_count=report.warned_sample_count,
            samples_with_missing_channels_count=report.samples_with_missing_channels_count,
            missing_channel_counts_by_channel=report.missing_channel_counts_by_channel,
        )
