from __future__ import annotations

from sceneops_core.common.ids import (
    generate_pipeline_run_id,
    generate_pipeline_step_run_id,
)
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.builtin import (
    BUILTIN_PIPELINE_DEFINITIONS,
    get_pipeline_definition,
)
from sceneops_core.pipelines.schemas import (
    CreatePipelineRunRequest,
    PipelineDefinition,
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunListResponse,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
    PipelineType,
)
from sceneops_core.jobs.schemas import JobType
from sceneops_db.repositories.pipelines import (
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
        self._pipeline_repository = pipeline_repository
        self._step_repository = step_repository
        self._default_dataset_id = default_dataset_id
        self._default_dataset_version = default_dataset_version

    # --- definitions (no DB) ---

    def list_pipeline_definitions(self) -> list[PipelineDefinition]:
        return list(BUILTIN_PIPELINE_DEFINITIONS)

    def get_pipeline_definition(
        self, pipeline_type: PipelineType
    ) -> PipelineDefinition | None:
        try:
            return get_pipeline_definition(pipeline_type)
        except KeyError:
            return None

    # --- pipeline runs ---

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
            dataset_id=request.dataset_id or self._default_dataset_id,
            dataset_version=request.dataset_version or self._default_dataset_version,
            model_id=request.model_id,
            model_version=request.model_version,
            params=request.params,
            created_at=now,
            updated_at=now,
        )

        created_pipeline = await self._pipeline_repository.create(pipeline_run)

        created_steps: list[PipelineStepRunManifest] = []
        for step_def in sorted(definition.steps, key=lambda s: s.order):
            step_params = {
                **step_def.default_params,
                **request.params.get(step_def.pipeline_step_id, {}),
            }
            step = PipelineStepRunManifest(
                pipeline_step_run_id=generate_pipeline_step_run_id(),
                pipeline_run_id=pipeline_run.pipeline_run_id,
                pipeline_step_id=step_def.pipeline_step_id,
                pipeline_step_name=step_def.name,
                step_order=step_def.order,
                status=PipelineStepRunStatus.PENDING,
                job_type=JobType(step_def.job_type),
                depends_on_step_ids=step_def.depends_on_pipeline_step_ids,
                params=step_params,
                created_at=now,
                updated_at=now,
            )
            created = await self._step_repository.create(step)
            created_steps.append(created)

        return PipelineRunDetailResponse(
            pipeline_run=created_pipeline,
            steps=created_steps,
        )

    async def get_pipeline_run(
        self, pipeline_run_id: str
    ) -> PipelineRunDetailResponse | None:
        pipeline_run = await self._pipeline_repository.get(pipeline_run_id)
        if pipeline_run is None:
            return None
        steps = await self._step_repository.list_for_pipeline_run(pipeline_run_id)
        return PipelineRunDetailResponse(pipeline_run=pipeline_run, steps=steps)

    async def list_pipeline_runs(
        self,
        *,
        status: PipelineRunStatus | None = None,
        pipeline_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> PipelineRunListResponse:
        runs = await self._pipeline_repository.list(
            type=pipeline_type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            limit=limit,
            offset=offset,
        )
        return PipelineRunListResponse(pipeline_runs=runs, count=len(runs))

    async def list_pipeline_step_runs(
        self, pipeline_run_id: str
    ) -> PipelineStepRunListResponse | None:
        run = await self._pipeline_repository.get(pipeline_run_id)
        if run is None:
            return None
        steps = await self._step_repository.list_for_pipeline_run(pipeline_run_id)
        return PipelineStepRunListResponse(steps=steps, count=len(steps))

    async def validate_executable(self, pipeline_run_id: str) -> PipelineRunManifest:
        run = await self._pipeline_repository.get(pipeline_run_id)
        if run is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")
        blocked = {
            PipelineRunStatus.RUNNING,
            PipelineRunStatus.SUCCEEDED,
            PipelineRunStatus.CANCELLED,
        }
        if run.status in blocked:
            raise ValueError(
                f"Pipeline run is not executable: "
                f"pipeline_run_id={pipeline_run_id}, status={run.status}"
            )
        return run
