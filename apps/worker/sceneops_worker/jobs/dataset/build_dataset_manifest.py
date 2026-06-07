from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.datasets.schemas.manifests import (
    DatasetManifest,
    DatasetSceneIndexEntry,
)
from sceneops_core.jobs.schemas import (
    BuildDatasetManifestJobParams,
    BuildDatasetManifestJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class BuildDatasetManifestJobHandler(
    JobHandler[BuildDatasetManifestJobParams, BuildDatasetManifestJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_DATASET_MANIFEST

    @property
    def params_model(self) -> type[BuildDatasetManifestJobParams]:
        return BuildDatasetManifestJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        scene_manifest_uris = inputs.refs.get("scene_manifest_uris") or []
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "scene_manifest_uris": scene_manifest_uris,
        }

    async def run(
        self,
        request: JobHandlerRequest[BuildDatasetManifestJobParams],
    ) -> BuildDatasetManifestJobResult:
        job = request.job
        params = request.params
        context = request.context

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        uris = params.scene_manifest_uris

        if not uris:
            uris = list(
                await context.scene_store.list(
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                    limit=10_000,
                )
                or []
            )
            uris = [
                s.scene_manifest_uri for s in uris if s.scene_manifest_uri is not None
            ]

        scenes: list[DatasetSceneIndexEntry] = []
        total_samples = 0
        total_frames = 0
        all_channels: set[str] = set()

        for uri in uris:
            scene_manifest = await context.scene_artifact_store.load_scene_manifest(uri)
            if scene_manifest is None:
                continue

            scenes.append(
                DatasetSceneIndexEntry(
                    scene_id=scene_manifest.scene_id,
                    scene_manifest_uri=uri,
                    sample_count=scene_manifest.sample_count,
                    frame_count=scene_manifest.frame_count,
                    channels=scene_manifest.channels,
                )
            )
            total_samples += scene_manifest.sample_count
            total_frames += scene_manifest.frame_count
            all_channels.update(scene_manifest.channels)

        channels = sorted(all_channels)

        manifest = DatasetManifest(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_count=len(scenes),
            sample_count=total_samples,
            frame_count=total_frames,
            channels=channels,
            scenes=scenes,
            created_at=utc_now(),
        )

        dataset_manifest_uri = (
            await context.dataset_artifact_store.write_dataset_manifest(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                manifest=manifest,
            )
        )

        version = await context.dataset_store.get_version(
            dataset_id=dataset_id, version=dataset_version
        )
        if version is not None:
            await context.dataset_store.save_version(
                version.model_copy(
                    update={
                        "status": DatasetVersionStatus.READY,
                        "manifest_uri": dataset_manifest_uri,
                        "scene_count": len(scenes),
                        "sample_count": total_samples,
                        "frame_count": total_frames,
                        "channels": channels,
                    }
                )
            )

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.DATASET_MANIFEST,
                uri=dataset_manifest_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.DATASET_VERSION,
            owner_id=f"{dataset_id}:{dataset_version}",
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        return BuildDatasetManifestJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_manifest_uri=dataset_manifest_uri,
            scene_count=len(scenes),
            sample_count=total_samples,
            frame_count=total_frames,
        )
