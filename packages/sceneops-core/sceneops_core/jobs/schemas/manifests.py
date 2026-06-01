from __future__ import annotations

from datetime import datetime
from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict, ErrorInfo

from .enums import JobStatus, JobType
from .steps import JobStep


class JobManifest(SceneOpsBaseModel):
    job_id: str
    type: JobType
    status: JobStatus

    dataset_id: str
    dataset_version: str

    params: JsonDict = Field(default_factory=dict)
    steps: list[JobStep] = Field(default_factory=list)

    result: JsonDict | None = None
    error: ErrorInfo | None = None

    # Pipeline linkage
    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    pipeline_step_name: str | None = None

    # Orchestration fields
    retry_count: int = 0
    max_retries: int = 0

    worker_id: str | None = None
    queued_at: datetime | None = None
    locked_at: datetime | None = None
    heartbeat_at: datetime | None = None

    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
