from __future__ import annotations

from pydantic import BaseModel

from sceneops_core.schemas.pipelines.manifests import (
    PipelineRunManifest,
    PipelineStepRunManifest,
)


class PipelineRunListResponse(BaseModel):
    pipelineRuns: list[PipelineRunManifest]
    count: int


class PipelineStepRunListResponse(BaseModel):
    steps: list[PipelineStepRunManifest]
    count: int


class PipelineRunDetailResponse(BaseModel):
    pipelineRun: PipelineRunManifest
    steps: list[PipelineStepRunManifest]
