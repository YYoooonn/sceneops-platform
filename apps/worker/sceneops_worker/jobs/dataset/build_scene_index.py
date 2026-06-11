from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetSceneIndexEntry
from sceneops_core.jobs.schemas import (
    BuildSceneIndexJobParams,
    BuildSceneIndexJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class BuildSceneIndexJobHandler(
    JobHandler[BuildSceneIndexJobParams, BuildSceneIndexJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SCENE_INDEX

    @property
    def params_model(self) -> type[BuildSceneIndexJobParams]:
        return BuildSceneIndexJobParams

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
        request: JobHandlerRequest[BuildSceneIndexJobParams],
    ) -> BuildSceneIndexJobResult:
        params = request.params
        context = request.context

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        # DatasetManifest is a derived snapshot of SceneRecord rows.
        # Always query all registered scenes — never build from pipeline batch input only.
        all_scene_records = await context.scene_store.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            limit=10_000,
        )

        uris = [
            s.scene_manifest_uri
            for s in all_scene_records
            if s.scene_manifest_uri is not None
        ]

        if not uris:
            raise ValueError(
                f"build_scene_index: no registered scenes found for "
                f"dataset_id={dataset_id!r}, dataset_version={dataset_version!r}. "
                "Ensure register_scene has completed before building the index."
            )

        entries: list[DatasetSceneIndexEntry] = []
        total_samples = 0
        total_frames = 0

        for uri in uris:
            manifest = await context.scene_artifact_store.load_scene_manifest(uri)
            if manifest is None:
                continue

            entries.append(
                DatasetSceneIndexEntry(
                    scene_id=manifest.scene_id,
                    scene_manifest_uri=uri,
                    sample_count=manifest.sample_count,
                    frame_count=manifest.frame_count,
                    channels=manifest.channels,
                )
            )
            total_samples += manifest.sample_count
            total_frames += manifest.frame_count

        scene_index_uri = await context.scene_artifact_store.write_scene_index(
            dataset_id=dataset_id or "",
            dataset_version=dataset_version or "",
            entries=entries,
        )

        return BuildSceneIndexJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_index_uri=scene_index_uri,
            scene_manifest_uris=uris,
            scene_count=len(entries),
            sample_count=total_samples,
            frame_count=total_frames,
        )
