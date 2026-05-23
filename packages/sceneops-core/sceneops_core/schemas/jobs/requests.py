from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.jobs.enums import JobType


class CreateJobRequest(BaseModel):
    type: JobType

    datasetId: str | None = None
    datasetVersion: str | None = None

    params: JsonDict = Field(default_factory=dict)

    pipelineRunId: str | None = None
    pipelineStepRunId: str | None = None
    pipelineStepName: str | None = None

    maxRetries: int = 0
