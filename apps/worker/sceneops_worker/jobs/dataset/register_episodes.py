from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterEpisodesJobParams,
    RegisterEpisodesJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.provenance import RevisionDecision
from sceneops_worker.episodes.registration import register_episodes
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class RegisterEpisodesJobHandler(
    JobHandler[RegisterEpisodesJobParams, RegisterEpisodesJobResult]
):
    """Canonical Episode revision registration; see
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
            **inputs.params,
            "manifest_artifact_ids": inputs.refs.get("manifest_artifact_ids") or [],
        }

    async def run(
        self, request: JobHandlerRequest[RegisterEpisodesJobParams]
    ) -> RegisterEpisodesJobResult:
        registration = await register_episodes(
            context=request.context,
            manifest_artifact_ids=request.params.manifest_artifact_ids,
        )
        return RegisterEpisodesJobResult(
            source_kind=registration.source_kind,
            source_id=registration.source_id,
            producer_fingerprint=registration.producer_fingerprint,
            revisions=registration.pins,
            episode_ids=registration.episode_ids(),
            new_unit_episode_ids=registration.episode_ids(RevisionDecision.NEW_UNIT),
            new_revision_episode_ids=registration.episode_ids(
                RevisionDecision.NEW_REVISION
            ),
            existing_revision_episode_ids=registration.episode_ids(
                RevisionDecision.EXISTING_REVISION
            ),
            revision_count=len(registration.revisions),
        )
