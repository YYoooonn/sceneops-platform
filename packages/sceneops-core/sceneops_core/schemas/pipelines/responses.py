from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.pipelines.manifests import (
    PipelineRunManifest,
    PipelineStepRunManifest,
)


class PipelineRunListResponse(SceneOpsBaseModel):
    pipeline_runs: list[PipelineRunManifest]
    count: int


class PipelineStepRunListResponse(SceneOpsBaseModel):
    steps: list[PipelineStepRunManifest]
    count: int


class PipelineRunDetailResponse(SceneOpsBaseModel):
    pipeline_run: PipelineRunManifest
    steps: list[PipelineStepRunManifest]
