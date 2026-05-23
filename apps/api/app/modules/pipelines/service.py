from __future__ import annotations

from sceneops_core.ids.pipelines import (
    generate_pipeline_run_id,
    generate_pipeline_step_run_id,
)
from sceneops_core.schemas.pipelines import (
    CreatePipelineRunRequest,
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_core.time import utc_now_iso
from sceneops_db.pipelines import (
    PipelineRunRepository,
    PipelineStepRunRepository,
)

from app.modules.pipelines.definitions import get_pipeline_definition


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
        now = utc_now_iso()
        definition = get_pipeline_definition(request.type)

        pipeline_run = PipelineRunManifest(
            pipelineRunId=generate_pipeline_run_id(),
            type=request.type,
            status=PipelineRunStatus.PENDING,
            datasetId=request.datasetId or self.default_dataset_id,
            datasetVersion=request.datasetVersion or self.default_dataset_version,
            modelId=request.modelId,
            modelVersion=request.modelVersion,
            params=request.params,
            createdAt=now,
            updatedAt=now,
        )

        created_pipeline = await self.pipeline_repository.create(pipeline_run)

        steps = [
            PipelineStepRunManifest(
                pipelineStepRunId=generate_pipeline_step_run_id(),
                pipelineRunId=created_pipeline.pipelineRunId,
                stepName=step.name,
                stepOrder=step.order,
                status=PipelineStepRunStatus.PENDING,
                jobType=step.jobType,
                jobId=None,
                dependsOnStepNames=step.dependsOn,
                params={
                    **step.defaultParams,
                    **request.params.get(step.name, {}),
                },
                createdAt=now,
                updatedAt=now,
            )
            for step in sorted(definition.steps, key=lambda item: item.order)
        ]

        created_steps = await self.step_repository.create_many(steps)

        return PipelineRunDetailResponse(
            pipelineRun=created_pipeline,
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
            pipelineRun=pipeline_run,
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
            pipelineRuns=pipeline_runs,
            count=len(pipeline_runs),
        )
