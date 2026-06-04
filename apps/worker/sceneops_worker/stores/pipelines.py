from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineType,
)
from sceneops_db.postgres import (
    PostgresPipelineRunRepository,
    PostgresPipelineStepRunRepository,
)


class PipelineStore:
    def __init__(self, session: AsyncSession) -> None:
        self._runs = PostgresPipelineRunRepository(session)
        self._steps = PostgresPipelineStepRunRepository(session)

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

    async def get_step(
        self, pipeline_step_run_id: str
    ) -> PipelineStepRunManifest | None:
        return await self._steps.get(pipeline_step_run_id)

    async def list_steps(
        self,
        pipeline_run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineStepRunManifest]:
        return await self._steps.list_for_pipeline_run(
            pipeline_run_id, limit=limit, offset=offset
        )

    async def create_step(
        self, step: PipelineStepRunManifest
    ) -> PipelineStepRunManifest:
        return await self._steps.create(step)

    async def save_step(self, step: PipelineStepRunManifest) -> PipelineStepRunManifest:
        return await self._steps.update(step)
