from __future__ import annotations

from sceneops_core.common.ids import (
    generate_pipeline_run_id,
    generate_pipeline_task_run_id,
)
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.builtin import (
    BUILTIN_PIPELINE_DEFINITIONS,
    get_pipeline_definition,
)
from sceneops_core.pipelines.schemas import (
    CreatePipelineRunRequest,
    PipelineDefinition,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineType,
)
from app.platform.pipelines.schemas import (
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineTaskRunListResponse,
)
from sceneops_core.jobs.schemas import JobType
from sceneops_db.repositories.pipelines import (
    PipelineRunRepository,
    PipelineTaskRunRepository,
)


class PipelineService:
    def __init__(
        self,
        *,
        pipeline_repository: PipelineRunRepository,
        task_repository: PipelineTaskRunRepository,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self._pipeline_repository = pipeline_repository
        self._task_repository = task_repository
        self._default_dataset_id = default_dataset_id
        self._default_dataset_version = default_dataset_version

    # --- definitions (no DB) ---

    def list_pipeline_definitions(
        self,
        *,
        include_experimental: bool = False,
    ) -> list[PipelineDefinition]:
        return [
            d
            for d in BUILTIN_PIPELINE_DEFINITIONS
            if d.supported
            and d.implemented
            and (include_experimental or not d.experimental)
        ]

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

        if not definition.supported or not definition.implemented:
            raise ValueError(
                f"Pipeline '{request.type}' is not currently supported because it "
                "contains unimplemented tasks."
            )

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

        created_tasks: list[PipelineTaskRunManifest] = []
        for task_def in sorted(definition.tasks, key=lambda t: t.order):
            task_params = {
                **task_def.default_params,
                **request.params.get(task_def.pipeline_task_id, {}),
            }
            task = PipelineTaskRunManifest(
                pipeline_task_run_id=generate_pipeline_task_run_id(),
                pipeline_run_id=pipeline_run.pipeline_run_id,
                pipeline_task_id=task_def.pipeline_task_id,
                pipeline_task_name=task_def.name,
                task_order=task_def.order,
                status=PipelineTaskRunStatus.PENDING,
                job_type=JobType(task_def.job_type),
                depends_on_task_ids=task_def.depends_on_pipeline_task_ids,
                params=task_params,
                created_at=now,
                updated_at=now,
            )
            created = await self._task_repository.create(task)
            created_tasks.append(created)

        return PipelineRunDetailResponse(
            pipeline_run=created_pipeline,
            tasks=created_tasks,
        )

    async def get_pipeline_run(
        self, pipeline_run_id: str
    ) -> PipelineRunDetailResponse | None:
        pipeline_run = await self._pipeline_repository.get(pipeline_run_id)
        if pipeline_run is None:
            return None
        tasks = await self._task_repository.list_for_pipeline_run(pipeline_run_id)
        return PipelineRunDetailResponse(pipeline_run=pipeline_run, tasks=tasks)

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

    async def list_pipeline_task_runs(
        self, pipeline_run_id: str
    ) -> PipelineTaskRunListResponse | None:
        run = await self._pipeline_repository.get(pipeline_run_id)
        if run is None:
            return None
        tasks = await self._task_repository.list_for_pipeline_run(pipeline_run_id)
        return PipelineTaskRunListResponse(tasks=tasks, count=len(tasks))

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

    async def mark_queued(self, pipeline_run_id: str) -> PipelineRunManifest:
        run = await self.validate_executable(pipeline_run_id)

        now = utc_now()
        run = run.model_copy(
            update={
                "status": PipelineRunStatus.QUEUED,
                "updated_at": now,
            }
        )
        await self._pipeline_repository.update(run)

        return run
