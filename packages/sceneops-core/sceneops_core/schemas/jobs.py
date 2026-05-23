from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from sceneops_core.constants.jobs import (
    EVALUATE_DETECTION_STEPS,
    INGEST_NUSCENES_STEPS,
    PREDICT_MOCK_DETECTION_STEPS,
)


class JobType(str, Enum):
    INGEST_NUSCENES = "INGEST_NUSCENES"
    PREDICT_MOCK_DETECTION = "PREDICT_MOCK_DETECTION"
    EVALUATE_DETECTION = "EVALUATE_DETECTION"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class JobStepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class JobStep(BaseModel):
    name: str
    status: JobStepStatus = JobStepStatus.PENDING
    message: str | None = None
    startedAt: str | None = None
    finishedAt: str | None = None


class CreateJobRequest(BaseModel):
    type: JobType
    datasetId: str | None = None
    datasetVersion: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class JobManifest(BaseModel):
    jobId: str
    type: JobType
    status: JobStatus

    datasetId: str | None = None
    datasetVersion: str | None = None

    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[JobStep] = Field(default_factory=list)

    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

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


class JobListResponse(BaseModel):
    jobs: list[JobManifest]
    count: int


class JobEventType(str, Enum):
    JOB_CREATED = "job_created"
    JOB_STARTED = "job_started"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"

    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"

    JOB_RETRYING = "job_retrying"
    JOB_CANCELED = "job_canceled"


class JobEventLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class JobEventManifest(BaseModel):
    eventId: str
    jobId: str
    eventType: JobEventType
    level: JobEventLevel = JobEventLevel.INFO
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    createdAt: str


class JobEventListResponse(BaseModel):
    events: list[JobEventManifest]
    count: int


def build_default_steps(job_type: JobType) -> list[JobStep]:
    if job_type == JobType.INGEST_NUSCENES:
        return [JobStep(name=name) for name in INGEST_NUSCENES_STEPS]

    if job_type == JobType.PREDICT_MOCK_DETECTION:
        return [JobStep(name=name) for name in PREDICT_MOCK_DETECTION_STEPS]

    if job_type == JobType.EVALUATE_DETECTION:
        return [JobStep(name=name) for name in EVALUATE_DETECTION_STEPS]

    return []
