from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas.manifests import DatasetManifest
from sceneops_core.jobs.schemas import (
    BuildDatasetManifestJobParams,
    BuildDatasetManifestJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.scenes.indexing import index_entry_for, list_dataset_version_scenes


class BuildDatasetManifestJobHandler(
    JobHandler[BuildDatasetManifestJobParams, BuildDatasetManifestJobResult]
):
    """Derived dataset manifest over every registered Scene, each pinned to
    its current revision.

    It records where the derived manifest lives (``manifest_uri``) but never
    writes DatasetVersion summary counts: those belong to the Scene
    registrar."""

    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_DATASET_MANIFEST

    @property
    def params_model(self) -> type[BuildDatasetManifestJobParams]:
        return BuildDatasetManifestJobParams

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
        request: JobHandlerRequest[BuildDatasetManifestJobParams],
    ) -> BuildDatasetManifestJobResult:
        job = request.job
        params = request.params
        context = request.context
        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        scenes = await list_dataset_version_scenes(
            context, dataset_id=dataset_id, dataset_version=dataset_version
        )
        if not scenes:
            raise ValueError(
                f"build_dataset_manifest: no registered scenes found for "
                f"dataset_id={dataset_id!r}, dataset_version={dataset_version!r}."
            )

        entries = [await index_entry_for(context, scene) for scene in scenes]
        manifest = DatasetManifest(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_count=len(entries),
            keyframe_count=sum(e.keyframe_count for e in entries),
            observation_count=sum(e.observation_count for e in entries),
            observed_channels=sorted({c for e in entries for c in e.observed_channels}),
            scenes=entries,
            created_at=utc_now(),
        )

        dataset_manifest_uri = (
            await context.dataset_artifact_store.write_dataset_manifest(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                manifest=manifest,
            )
        )
        await context.dataset_store.update_scene_inputs(
            dataset_id=dataset_id,
            version=dataset_version,
            manifest_uri=dataset_manifest_uri,
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
            scene_count=manifest.scene_count,
            keyframe_count=manifest.keyframe_count,
            observation_count=manifest.observation_count,
        )
