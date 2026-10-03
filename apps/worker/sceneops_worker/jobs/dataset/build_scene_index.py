from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    BuildSceneIndexJobParams,
    BuildSceneIndexJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.scenes.indexing import index_entry_for, list_dataset_version_scenes


class BuildSceneIndexJobHandler(
    JobHandler[BuildSceneIndexJobParams, BuildSceneIndexJobResult]
):
    """Derived snapshot of all registered Scenes of a DatasetVersion, each
    pinned to its current revision. Never part of membership."""

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
        dataset_id = params.dataset_id or ""
        dataset_version = params.dataset_version or ""

        scenes = await list_dataset_version_scenes(
            context, dataset_id=dataset_id, dataset_version=dataset_version
        )
        if not scenes:
            raise ValueError(
                f"build_scene_index: no registered scenes found for "
                f"dataset_id={dataset_id!r}, dataset_version={dataset_version!r}."
            )

        entries = [await index_entry_for(context, scene) for scene in scenes]
        scene_index_uri = await context.scene_artifact_store.write_scene_index(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            entries=entries,
        )

        return BuildSceneIndexJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_index_uri=scene_index_uri,
            scene_count=len(entries),
            keyframe_count=sum(e.keyframe_count for e in entries),
            observation_count=sum(e.observation_count for e in entries),
        )
