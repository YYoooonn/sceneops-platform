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
        return {
            **inputs.params,
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": (
                inputs.dataset.dataset_version if inputs.dataset else None
            ),
        }

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
