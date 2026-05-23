from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict, ErrorInfo
from sceneops_core.schemas.jobs.enums import JobStatus, JobType
from sceneops_core.schemas.jobs.steps import JobStep


class JobManifest(BaseModel):
    jobId: str
    type: JobType
    status: JobStatus

    datasetId: str
    datasetVersion: str

    params: JsonDict = Field(default_factory=dict)
    steps: list[JobStep] = Field(default_factory=list)

    result: JsonDict | None = None
    error: ErrorInfo | None = None

    # Pipeline linkage
    pipelineRunId: str | None = None
    pipelineStepRunId: str | None = None
    pipelineStepName: str | None = None

    # Orchestration fields
    retryCount: int = 0
    maxRetries: int = 0

    workerId: str | None = None
    queuedAt: str | None = None
    lockedAt: str | None = None
    heartbeatAt: str | None = None

    createdAt: str
    updatedAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
