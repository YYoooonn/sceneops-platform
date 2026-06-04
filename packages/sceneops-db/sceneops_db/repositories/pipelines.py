from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineType,
)


@runtime_checkable
class PipelineRunRepository(Protocol):
    async def create(self, run: PipelineRunManifest) -> PipelineRunManifest: ...

    async def get(self, pipeline_run_id: str) -> PipelineRunManifest | None: ...

    async def update(self, run: PipelineRunManifest) -> PipelineRunManifest: ...

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
    ) -> list[PipelineRunManifest]: ...


@runtime_checkable
class PipelineStepRunRepository(Protocol):
    async def create(
        self, step: PipelineStepRunManifest
    ) -> PipelineStepRunManifest: ...

    async def get(
        self,
        pipeline_step_run_id: str,
    ) -> PipelineStepRunManifest | None: ...

    async def update(
        self, step: PipelineStepRunManifest
    ) -> PipelineStepRunManifest: ...

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineStepRunManifest]: ...

    async def get_by_step_id(
        self,
        *,
        pipeline_run_id: str,
        step_id: str,
    ) -> PipelineStepRunManifest | None: ...
