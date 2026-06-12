from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
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

    async def count_by_status(self) -> dict[str, int]: ...


@runtime_checkable
class PipelineTaskRunRepository(Protocol):
    async def create(
        self, task: PipelineTaskRunManifest
    ) -> PipelineTaskRunManifest: ...

    async def get(
        self,
        pipeline_task_run_id: str,
    ) -> PipelineTaskRunManifest | None: ...

    async def update(
        self, task: PipelineTaskRunManifest
    ) -> PipelineTaskRunManifest: ...

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PipelineTaskRunManifest]: ...

    async def get_by_task_id(
        self,
        *,
        pipeline_run_id: str,
        task_id: str,
    ) -> PipelineTaskRunManifest | None: ...
