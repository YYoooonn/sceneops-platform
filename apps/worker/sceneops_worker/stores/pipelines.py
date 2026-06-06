from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineType,
)
from sceneops_db.postgres import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)


class PipelineStore:
    def __init__(self, session: AsyncSession) -> None:
        self._runs = PostgresPipelineRunRepository(session)
        self._tasks = PostgresPipelineTaskRunRepository(session)

    async def get(self, pipeline_run_id: str) -> PipelineRunManifest | None:
        return await self._runs.get(pipeline_run_id)

    async def create(self, run: PipelineRunManifest) -> PipelineRunManifest:
        return await self._runs.create(run)

    async def save(self, run: PipelineRunManifest) -> PipelineRunManifest:
        return await self._runs.update(run)

    async def list(
        self,
        *,
        type: PipelineType | None = None,
        status: PipelineRunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineRunManifest]:
        return await self._runs.list(
            type=type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            limit=limit,
            offset=offset,
        )

    async def get_task(
        self, pipeline_task_run_id: str
    ) -> PipelineTaskRunManifest | None:
        return await self._tasks.get(pipeline_task_run_id)

    async def list_tasks(
        self,
        pipeline_run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineTaskRunManifest]:
        return await self._tasks.list_for_pipeline_run(
            pipeline_run_id, limit=limit, offset=offset
        )

    async def create_task(
        self, task: PipelineTaskRunManifest
    ) -> PipelineTaskRunManifest:
        return await self._tasks.create(task)

    async def save_task(self, task: PipelineTaskRunManifest) -> PipelineTaskRunManifest:
        return await self._tasks.update(task)
