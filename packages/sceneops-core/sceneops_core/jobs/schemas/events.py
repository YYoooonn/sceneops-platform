from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import ErrorInfo, JsonDict, SceneOpsBaseModel

from .enums import JobEventLevel, JobEventType, JobStatus, JobStepStatus, JobType


class JobEvent(SceneOpsBaseModel):
    event_id: str
    job_id: str

    type: JobEventType

    job_type: JobType | None = None
    level: JobEventLevel = JobEventLevel.INFO

    # Current or target job status after this event.
    status: JobStatus | None = None

    # Optional step context.
    step_id: str | None = None
    step_name: str | None = None
    step_status: JobStepStatus | None = None

    # Optional pipeline context snapshot.
    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    pipeline_step_id: str | None = None

    # Worker/runtime context.
    worker_id: str | None = None
    attempt: int | None = None

    message: str | None = None

    error: ErrorInfo | None = None

    data: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
