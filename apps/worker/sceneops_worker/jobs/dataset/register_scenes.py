from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterScenesJobParams,
    RegisterScenesJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.scenes.registration import register_scenes


class RegisterScenesJobHandler(
    JobHandler[RegisterScenesJobParams, RegisterScenesJobResult]
):
    """Canonical Scene registration; see ``sceneops_worker.scenes.registration``.
    Fails the job, with no canonical change, on any rejected manifest, scope
    error or conflict."""

    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_SCENES

    @property
    def params_model(self) -> type[RegisterScenesJobParams]:
        return RegisterScenesJobParams

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
        self,
        request: JobHandlerRequest[RegisterScenesJobParams],
    ) -> RegisterScenesJobResult:
        params = request.params
        registration = await register_scenes(
            context=request.context,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            manifest_artifact_ids=params.manifest_artifact_ids,
            replace=params.replace,
        )
        return RegisterScenesJobResult(
            dataset_id=registration.dataset_id,
            dataset_version=registration.dataset_version,
            scene_ids=[s.scene_id for s in registration.scenes],
            manifest_artifact_ids=[s.manifest_artifact_id for s in registration.scenes],
            created_scene_ids=registration.created_scene_ids,
            replaced_scene_ids=registration.replaced_scene_ids,
            unchanged_scene_ids=registration.unchanged_scene_ids,
            removed_scene_ids=registration.removed_scene_ids,
            registered_scene_count=len(registration.scenes),
        )
