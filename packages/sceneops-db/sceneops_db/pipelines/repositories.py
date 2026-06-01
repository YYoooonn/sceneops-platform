from __future__ import annotations

from typing import Protocol

from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)


class PipelineRunRepository(Protocol):
    async def create(self, manifest: PipelineRunManifest) -> PipelineRunManifest:
        ...

    async def get(self, pipeline_run_id: str) -> PipelineRunManifest:
        ...

    async def list(
        self,
        *,
        status: PipelineRunStatus | None = None,
        pipeline_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[PipelineRunManifest]:
        ...

    async def update(self, manifest: PipelineRunManifest) -> PipelineRunManifest:
        ...


class PipelineStepRunRepository(Protocol):
    async def create(self, manifest: PipelineStepRunManifest) -> PipelineStepRunManifest:
        ...

    async def create_many(
        self,
        manifests: list[PipelineStepRunManifest],
    ) -> list[PipelineStepRunManifest]:
        ...

    async def get(self, pipeline_step_run_id: str) -> PipelineStepRunManifest:
        ...

    async def list_by_pipeline_run(
        self,
        pipeline_run_id: str,
    ) -> list[PipelineStepRunManifest]:
        ...

    async def update(
        self,
        manifest: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest:
        ...

    async def update_status(
        self,
        pipeline_step_run_id: str,
        status: PipelineStepRunStatus,
    ) -> PipelineStepRunManifest:
        ...
