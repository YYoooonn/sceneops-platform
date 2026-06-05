from __future__ import annotations

from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetType, DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    IngestScenesJobParams,
    IngestScenesJobResult,
    JobType,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.datasets.ingestion.nuscenes_scene import (
    build_scene_manifest,
    build_scene_record,
)
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class IngestScenesJobHandler(JobHandler[IngestScenesJobParams, IngestScenesJobResult]):
    @property
    def job_type(self) -> JobType:
        return JobType.INGEST_SCENES

    @property
    def params_model(self) -> type[IngestScenesJobParams]:
        return IngestScenesJobParams

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        return base

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = IngestScenesJobResult.model_validate(result)
        return {
            "scene_ids": parsed.scene_ids,
            "scene_manifest_uris": parsed.scene_manifest_uris,
            "scene_count": parsed.scene_count,
            "sample_count": parsed.sample_count,
            "frame_count": parsed.frame_count,
            "channels": parsed.channels,
        }

    async def run(
        self,
        request: JobHandlerRequest[IngestScenesJobParams],
    ) -> IngestScenesJobResult:
        job = request.job
        params = request.params
        context = request.context

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

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

        scene_ids: list[str] = []
        scene_manifest_uris: list[str] = []
        total_samples = 0
        total_frames = 0
        all_channels: set[str] = set()

        scenes = await _ingest_scenes(params=params, context=context, job=job)

        for scene_id, scene_manifest_uri, manifest in scenes:
            scene_ids.append(scene_id)
            scene_manifest_uris.append(scene_manifest_uri)
            total_samples += manifest.sample_count
            total_frames += manifest.frame_count
            all_channels.update(manifest.channels)

            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.SCENE_MANIFEST,
                    uri=scene_manifest_uri,
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

        channels = sorted(all_channels)

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

        return IngestScenesJobResult(
            scene_ids=scene_ids,
            scene_manifest_uris=scene_manifest_uris,
            scene_count=len(scene_ids),
            sample_count=total_samples,
            frame_count=total_frames,
            channels=channels,
        )


async def _ingest_scenes(
    *,
    params: IngestScenesJobParams,
    context: WorkerContext,
    job: Any,
) -> list[tuple[str, str, Any]]:
    if params.source_format == DatasetType.NUSCENES:
        return await _ingest_nuscenes_scenes(params=params, context=context, job=job)
    raise ValueError(f"Unsupported source_format: {params.source_format}")


async def _ingest_nuscenes_scenes(
    *,
    params: IngestScenesJobParams,
    context: WorkerContext,
    job: Any,
) -> list[tuple[str, str, Any]]:
    from nuscenes.nuscenes import NuScenes

    dataset_id = params.dataset_id
    dataset_version = params.dataset_version

    nusc = NuScenes(
        version=dataset_version,
        dataroot=params.source_root_uri,
        verbose=False,
    )

    scenes = nusc.scene
    if params.source_scene_ids:
        scene_names = set(params.source_scene_ids)
        scenes = [s for s in scenes if s["name"] in scene_names]

    if params.max_scenes is not None:
        scenes = scenes[: params.max_scenes]

    results: list[tuple[str, str, Any]] = []

    for ns_scene in scenes:
        scene_id = ns_scene["name"]

        manifest = build_scene_manifest(
            nusc=nusc,
            scene=ns_scene,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
        )

        scene_manifest_uri = await context.scene_artifact_store.write_scene_manifest(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            manifest=manifest,
        )

        scene_record = build_scene_record(
            scene_id=scene_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            manifest=manifest,
            scene_manifest_uri=scene_manifest_uri,
        )

        await context.scene_store.upsert(scene_record)

        results.append((scene_id, scene_manifest_uri, manifest))

    return results
