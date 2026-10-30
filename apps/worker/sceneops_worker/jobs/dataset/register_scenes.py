from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterScenesJobParams,
    RegisterScenesJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.provenance import RevisionDecision
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.scenes.registration import register_scenes


class RegisterScenesJobHandler(
    JobHandler[RegisterScenesJobParams, RegisterScenesJobResult]
):
    """Canonical Scene revision registration; see
    ``sceneops_worker.scenes.registration``. Fails the job, with no
    canonical change, on any rejected manifest, scope error or conflict."""

    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_SCENES

    @property
    def params_model(self) -> type[RegisterScenesJobParams]:
        return RegisterScenesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            **inputs.params,
            "manifest_artifact_ids": inputs.refs.get("manifest_artifact_ids") or [],
        }

    async def run(
        self,
        request: JobHandlerRequest[RegisterScenesJobParams],
    ) -> RegisterScenesJobResult:
        registration = await register_scenes(
            context=request.context,
            manifest_artifact_ids=request.params.manifest_artifact_ids,
        )
        return RegisterScenesJobResult(
            source_kind=registration.source_kind,
            source_id=registration.source_id,
            producer_fingerprint=registration.producer_fingerprint,
            revisions=registration.pins,
            scene_ids=registration.scene_ids(),
            new_unit_scene_ids=registration.scene_ids(RevisionDecision.NEW_UNIT),
            new_revision_scene_ids=registration.scene_ids(
                RevisionDecision.NEW_REVISION
            ),
            existing_revision_scene_ids=registration.scene_ids(
                RevisionDecision.EXISTING_REVISION
            ),
            revision_count=len(registration.revisions),
        )
