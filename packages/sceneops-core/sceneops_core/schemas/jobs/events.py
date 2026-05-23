from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict


class JobEventType(str, Enum):
    JOB_CREATED = "job_created"
    JOB_STARTED = "job_started"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"

    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"

    JOB_CANCELED = "job_canceled"
    JOB_RETRYING = "job_retrying"


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
    payload: JsonDict = Field(default_factory=dict)
    createdAt: str
