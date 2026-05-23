from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict, ErrorInfo
from sceneops_core.schemas.pipelines.enums import (
    PipelineRunStatus,
    PipelineStepRunStatus,
    PipelineType,
)


class PipelineRunManifest(BaseModel):
    pipelineRunId: str
    type: PipelineType
    status: PipelineRunStatus

    datasetId: str
    datasetVersion: str

    modelId: str | None = None
    modelVersion: str | None = None

    params: JsonDict = Field(default_factory=dict)
    result: JsonDict | None = None
    error: ErrorInfo | None = None

    createdAt: str
    updatedAt: str
    startedAt: str | None = None
    finishedAt: str | None = None


class PipelineStepRunManifest(BaseModel):
    pipelineStepRunId: str
    pipelineRunId: str

    stepName: str
    stepOrder: int
    status: PipelineStepRunStatus

    jobType: str
    jobId: str | None = None

    dependsOnStepNames: list[str] = Field(default_factory=list)

    params: JsonDict = Field(default_factory=dict)
    result: JsonDict | None = None
    error: ErrorInfo | None = None

    createdAt: str
    updatedAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
