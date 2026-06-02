from __future__ import annotations

from typing import Protocol

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineStepRunManifest,
)
from sceneops_db.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineStepRunRepository,
)
from sceneops_db.session import async_session_scope


class PipelineStore(Protocol):
    async def get_pipeline_run(
        self,
        pipeline_run_id: str,
    ) -> PipelineRunManifest | None: ...

    async def save_pipeline_run(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> PipelineRunManifest: ...

    async def list_steps(
        self,
        pipeline_run_id: str,
    ) -> list[PipelineStepRunManifest]: ...

    async def save_step(
        self,
        step: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest: ...


class PostgresPipelineStore:
    async def get_pipeline_run(
        self,
        pipeline_run_id: str,
    ) -> PipelineRunManifest | None:
        async with async_session_scope() as session:
            repository = PostgresPipelineRunRepository(session)

            try:
                return await repository.get(pipeline_run_id)
            except FileNotFoundError:
                return None

    async def save_pipeline_run(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> PipelineRunManifest:
        async with async_session_scope() as session:
            repository = PostgresPipelineRunRepository(session)
            return await repository.update(pipeline_run)

    async def list_steps(
        self,
        pipeline_run_id: str,
    ) -> list[PipelineStepRunManifest]:
        async with async_session_scope() as session:
            repository = PostgresPipelineStepRunRepository(session)
            return await repository.list_by_pipeline_run(pipeline_run_id)

    async def save_step(
        self,
        step: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest:
        async with async_session_scope() as session:
            repository = PostgresPipelineStepRunRepository(session)
            return await repository.update(step)
