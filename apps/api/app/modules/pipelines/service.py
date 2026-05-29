from __future__ import annotations

from sceneops_core.ids.pipelines import (
    generate_pipeline_run_id,
    generate_pipeline_step_run_id,
)
from sceneops_core.pipelines import get_pipeline_definition
from sceneops_core.schemas.pipelines import (
    CreatePipelineRunRequest,
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_core.time import utc_now
from sceneops_db.pipelines import (
    PipelineRunRepository,
    PipelineStepRunRepository,
)


class PipelineService:
    def __init__(
        self,
        *,
        pipeline_repository: PipelineRunRepository,
        step_repository: PipelineStepRunRepository,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self.pipeline_repository = pipeline_repository
        self.step_repository = step_repository
        self.default_dataset_id = default_dataset_id
        self.default_dataset_version = default_dataset_version

    async def create_pipeline_run(
        self,
        request: CreatePipelineRunRequest,
    ) -> PipelineRunDetailResponse:
        now = utc_now()
        definition = get_pipeline_definition(request.type)

        pipeline_run = PipelineRunManifest(
            pipeline_run_id=generate_pipeline_run_id(),
            type=request.type,
            status=PipelineRunStatus.PENDING,
            dataset_id=request.dataset_id or self.default_dataset_id,
            dataset_version=request.dataset_version or self.default_dataset_version,
            model_id=request.model_id,
            model_version=request.model_version,
            params=request.params,
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )

        created_pipeline = await self.pipeline_repository.create(pipeline_run)

        steps = []
        for step_def in sorted(definition.steps, key=lambda item: item.order):
            step_params = {
                **step_def.default_params,
                **request.params.get(step_def.name, {}),
            }

            steps.append(
                PipelineStepRunManifest(
                    pipeline_step_run_id=generate_pipeline_step_run_id(),
                    pipeline_run_id=pipeline_run.pipeline_run_id,
                    step_name=step_def.name,
                    step_order=step_def.order,
                    status=PipelineStepRunStatus.PENDING,
                    job_type=step_def.job_type,
                    job_id=None,
                    depends_on_step_names=step_def.depends_on,
                    params=step_params,
                    result=None,
                    error=None,
                    created_at=now,
                    updated_at=now,
                )
            )

        created_steps = await self.step_repository.create_many(steps)

        return PipelineRunDetailResponse(
            pipeline_run=created_pipeline,
            steps=created_steps,
        )

    async def get_pipeline_run_detail(
        self,
        pipeline_run_id: str,
    ) -> PipelineRunDetailResponse | None:
        try:
            pipeline_run = await self.pipeline_repository.get(pipeline_run_id)
        except FileNotFoundError:
            return None

        steps = await self.step_repository.list_by_pipeline_run(pipeline_run_id)

        return PipelineRunDetailResponse(
            pipeline_run=pipeline_run,
            steps=steps,
        )

    async def list_pipeline_runs(
        self,
        *,
        status: PipelineRunStatus | None = None,
        pipeline_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> PipelineRunListResponse:
        pipeline_runs = await self.pipeline_repository.list(
            status=status,
            pipeline_type=pipeline_type,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )

        return PipelineRunListResponse(
            pipeline_runs=pipeline_runs,
            count=len(pipeline_runs),
        )

    async def validate_executable(
        self,
        pipeline_run_id: str,
    ) -> PipelineRunManifest:
        pipeline_run = await self.pipeline_repository.get(pipeline_run_id)

        blocked_statuses = {
            PipelineRunStatus.RUNNING,
            PipelineRunStatus.SUCCEEDED,
            PipelineRunStatus.CANCELED,
        }

        if pipeline_run.status in blocked_statuses:
            raise RuntimeError(
                f"Pipeline run is not executable. "
                f"pipeline_run_id={pipeline_run_id}, "
                f"status={pipeline_run.status.value}"
            )

        return pipeline_run
