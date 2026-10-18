from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    ComposeDatasetVersionJobParams,
    ComposeDatasetVersionJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class ComposeDatasetVersionJobHandler(
    JobHandler[ComposeDatasetVersionJobParams, ComposeDatasetVersionJobResult]
):
    """Immutable DatasetVersion composition; see
    ``sceneops_worker.datasets.composition``. Fails the job, writing
    nothing, on an invalid input or a name bound to another composition."""

    @property
    def job_type(self) -> JobType:
        return JobType.COMPOSE_DATASET_VERSION

    @property
    def params_model(self) -> type[ComposeDatasetVersionJobParams]:
        return ComposeDatasetVersionJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        """A pipeline stage composes exactly the revisions its register stage
        returned (the ``revisions`` REF) into the version its task params
        name. Members given in the task params are refused: they would
        compose something other than what the run built."""
        named = sorted({"pins", "selector"} & inputs.params.keys())
        if named:
            raise ValueError(
                f"compose_dataset_version in a pipeline composes the revisions "
                f"its register stage returned; {named} are not accepted"
            )
        pins = inputs.refs.get("revisions")
        if not pins:
            raise ValueError(
                "compose_dataset_version has no revisions to compose: its "
                "register stage returned none"
            )
        return {**inputs.params, "pins": pins}

    async def run(
        self,
        request: JobHandlerRequest[ComposeDatasetVersionJobParams],
    ) -> ComposeDatasetVersionJobResult:
        composition = await compose_dataset_version(
            context=request.context, params=request.params
        )
        version = composition.version
        return ComposeDatasetVersionJobResult(
            dataset_id=version.dataset_id,
            dataset_version=version.version,
            composition_checksum=version.composition_checksum,
            created=composition.created,
            member_count=composition.member_count,
            scene_count=version.scene.scene_count if version.scene else 0,
            episode_count=version.episode.episode_count if version.episode else 0,
        )
