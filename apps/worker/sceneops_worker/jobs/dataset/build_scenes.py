from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildScenesJobParams,
    BuildScenesJobResult,
    JobType,
)
from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawLogSourceType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.observations.artifacts import ObservationArtifactStore
from sceneops_worker.observations.adapters.factory import RawLogAdapterFactory
from sceneops_worker.scenes.raw_scene_builder import RawSceneBuilder


class BuildScenesJobHandler(JobHandler[BuildScenesJobParams, BuildScenesJobResult]):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SCENES

    @property
    def params_model(self) -> type[BuildScenesJobParams]:
        return BuildScenesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

    async def run(
        self,
        request: JobHandlerRequest[BuildScenesJobParams],
    ) -> BuildScenesJobResult:
        job = request.job
        params = request.params
        context = request.context

        dataset_id = params.dataset_id or context.default_dataset_id
        dataset_version = params.dataset_version or context.default_dataset_version

        # Ensure dataset version exists
        version = await context.dataset_store.get_version(
            dataset_id=dataset_id, version=dataset_version
        )
        if version is None:
            version = await context.dataset_store.create_version(
                DatasetVersionRecord(
                    dataset_id=dataset_id,
                    version=dataset_version,
                    status=DatasetVersionStatus.INGESTING,
                )
            )
        else:
            version = await context.dataset_store.save_version(
                version.model_copy(update={"status": DatasetVersionStatus.INGESTING})
            )

        # Build observation artifact store inline (no WorkerContext change needed)
        obs_store = ObservationArtifactStore(
            artifact_store=context.artifact_store,
            dataset_root_uri=context.settings.dataset_root_uri,
        )

        version_root_uri = context.scene_artifact_store._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )

        raw_log_id = params.raw_log_id or f"{dataset_id}-{dataset_version}"

        # Resolve or build RawLogManifest + RawLogFrameIndex
        if params.raw_log_manifest_uri and params.raw_log_frame_index_uri:
            raw_manifest_uri = params.raw_log_manifest_uri
            raw_frame_index_uri = params.raw_log_frame_index_uri
            raw_manifest, frame_index = await _load_raw_artifacts(
                obs_store=obs_store,
                manifest_uri=raw_manifest_uri,
                frame_index_uri=raw_frame_index_uri,
            )
        else:
            adapter_factory = _build_adapter_factory(params=params, obs_store=obs_store)
            source_type = params.source_type or RawLogSourceType.NUSCENES_RAW_LOG_MOCK
            adapter = adapter_factory.get(source_type)

            (
                raw_manifest,
                frame_index,
                raw_manifest_uri,
                raw_frame_index_uri,
            ) = await adapter.build_raw_log(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                raw_log_id=raw_log_id,
                version_root_uri=version_root_uri,
                params=params.model_dump(mode="python"),
            )

        # Build scenes from raw frames
        builder = RawSceneBuilder(
            scene_artifact_store=context.scene_artifact_store,
            observation_artifact_store=obs_store,
        )

        (
            scene_ids,
            scene_manifest_uris,
            segment_index_uri,
            total_samples,
            total_frames,
            obs_count,
        ) = await builder.build(
            manifest=raw_manifest,
            frame_index=frame_index,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            version_root_uri=version_root_uri,
            segmentation=params.segmentation,
            sampling=params.sampling,
            max_built_scenes=params.max_built_scenes,
        )

        channels = sorted(raw_manifest.channels)

        # Register artifact records for scene manifests
        for scene_id, uri in zip(scene_ids, scene_manifest_uris):
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.SCENE_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE,
                owner_id=scene_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                scene_id=scene_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        # Update dataset version
        await context.dataset_store.save_version(
            version.model_copy(
                update={
                    "status": DatasetVersionStatus.INGESTED,
                    "scene_count": len(scene_ids),
                    "sample_count": total_samples,
                    "frame_count": total_frames,
                    "channels": channels,
                }
            )
        )

        return BuildScenesJobResult(
            raw_log_id=raw_log_id,
            scene_ids=scene_ids,
            scene_manifest_uris=scene_manifest_uris,
            scene_count=len(scene_ids),
            sample_count=total_samples,
            frame_count=total_frames,
            scene_segment_index_uri=segment_index_uri,
            raw_log_manifest_uri=raw_manifest_uri,
            raw_log_frame_index_uri=raw_frame_index_uri,
            source_type=str(raw_manifest.source_type)
            if raw_manifest.source_type
            else None,
            source_format=str(raw_manifest.source_format),
            observation_count=obs_count,
            channels=channels,
            segmentation_strategy=str(params.segmentation.strategy),
            sampling_strategy=str(params.sampling.strategy),
        )


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


def _build_adapter_factory(
    *,
    params: BuildScenesJobParams,
    obs_store: ObservationArtifactStore,
) -> RawLogAdapterFactory:
    # pylint: disable=import-outside-toplevel
    from sceneops_worker.datasets.ingestion.nuscenes_raw_log import NuScenesRawLogMocker

    factory = RawLogAdapterFactory()

    source_root = params.raw_root_uri or params.records_uri or "/data/raw/nuscenes"
    required_channels = (
        set(params.sampling.required_channels)
        if params.sampling.required_channels
        else None
    )

    factory.register(
        RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
        NuScenesRawLogMocker(
            source_root_uri=source_root,
            observation_store=obs_store,
            required_channels=required_channels,
        ),
    )

    return factory
