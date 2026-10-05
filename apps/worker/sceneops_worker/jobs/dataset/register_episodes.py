from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterEpisodesJobParams,
    RegisterEpisodesJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.episodes.registration import register_episodes
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class RegisterEpisodesJobHandler(
    JobHandler[RegisterEpisodesJobParams, RegisterEpisodesJobResult]
):
    """Canonical Episode registration; see
    ``sceneops_worker.episodes.registration``. Fails the job, with no
    canonical change, on any rejected manifest, scope error or conflict."""

    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_EPISODES

    @property
    def params_model(self) -> type[RegisterEpisodesJobParams]:
        return RegisterEpisodesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "manifest_artifact_ids": inputs.refs.get("manifest_artifact_ids") or [],
        }

    async def run(
        self, request: JobHandlerRequest[RegisterEpisodesJobParams]
    ) -> RegisterEpisodesJobResult:
        params = request.params
        registration = await register_episodes(
            context=request.context,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            manifest_artifact_ids=params.manifest_artifact_ids,
            replace=params.replace,
        )
        return RegisterEpisodesJobResult(
            dataset_id=registration.dataset_id,
            dataset_version=registration.dataset_version,
            episode_ids=[e.episode_id for e in registration.episodes],
            manifest_artifact_ids=[
                e.manifest_artifact_id for e in registration.episodes
            ],
            created_episode_ids=registration.created_episode_ids,
            replaced_episode_ids=registration.replaced_episode_ids,
            unchanged_episode_ids=registration.unchanged_episode_ids,
            removed_episode_ids=registration.removed_episode_ids,
            registered_episode_count=len(registration.episodes),
        )
