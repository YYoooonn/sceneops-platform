from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .manifests import PipelineRunManifest, PipelineStepRunManifest


class PipelineRunListResponse(SceneOpsBaseModel):
    pipeline_runs: list[PipelineRunManifest]
    count: int

    metadata: JsonDict = Field(default_factory=dict)


class PipelineStepRunListResponse(SceneOpsBaseModel):
    steps: list[PipelineStepRunManifest]
    count: int

    metadata: JsonDict = Field(default_factory=dict)


class PipelineRunDetailResponse(SceneOpsBaseModel):
    pipeline_run: PipelineRunManifest
    steps: list[PipelineStepRunManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
