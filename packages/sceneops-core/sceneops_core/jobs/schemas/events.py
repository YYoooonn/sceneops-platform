from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict


class JobEventType(StrEnum):
    JOB_CREATED = "job_created"
    JOB_STARTED = "job_started"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"

    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"

    JOB_CANCELED = "job_canceled"
    JOB_RETRYING = "job_retrying"


class JobEventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class JobEventManifest(SceneOpsBaseModel):
    event_id: str
    job_id: str
    event_type: JobEventType
    level: JobEventLevel = JobEventLevel.INFO
    message: str | None = None
    payload: JsonDict = Field(default_factory=dict)
    created_at: datetime | None = None
