from __future__ import annotations

from datetime import datetime

from pydantic import Field
from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import (
    JobEventLevel,
    JobEventType,
    JobStatus,
    JobType,
)
from sceneops_core.pipelines.schemas import (
    PipelineRunStatus,
    PipelineStepRunStatus,
    PipelineType,
)


class JobTimelineEvent(SceneOpsBaseModel):
    event_id: str | None = None
    event_type: JobEventType
    level: JobEventLevel
    message: str | None = None
    payload: JsonDict = Field(default_factory=dict)
    created_at: datetime | None = None


class JobTimelineStep(SceneOpsBaseModel):
    job_step_id: str
    name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class JobTimelineResponse(SceneOpsBaseModel):
    job_id: str
    job_type: JobType
    status: JobStatus
    worker_id: str | None = None

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    pipeline_step_id: str | None = None

    queued_at: datetime | None = None
    locked_at: datetime | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None

    queue_latency_ms: int | None = None
    duration_ms: int | None = None
    total_elapsed_ms: int | None = None

    error_type: str | None = None
    error_message: str | None = None

    steps: list[JobTimelineStep]
    events: list[JobTimelineEvent]


class PipelineStepTimelineItem(SceneOpsBaseModel):
    pipeline_step_run_id: str
    pipeline_step_id: str
    pipeline_step_name: str
    step_order: int
    job_type: JobType
    job_id: str | None = None
    status: PipelineStepRunStatus

    depends_on_pipeline_step_ids: list[str] = Field(default_factory=list)

    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None

    error_type: str | None = None
    error_message: str | None = None


class PipelineTimelineResponse(SceneOpsBaseModel):
    pipeline_run_id: str
    pipeline_type: PipelineType
    status: PipelineRunStatus

    dataset_id: str | None = None
    dataset_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None

    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    queue_latency_ms: int | None = None
    duration_ms: int | None = None
    total_elapsed_ms: int | None = None

    error_type: str | None = None
    error_message: str | None = None

    steps: list[PipelineStepTimelineItem]


class StatusCount(SceneOpsBaseModel):
    status: str
    count: int


class RecentFailureItem(SceneOpsBaseModel):
    resource_type: str
    resource_id: str
    resource_kind: str | None = None
    status: str

    error_type: str | None = None
    error_message: str | None = None

    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None


class OperationsSummaryResponse(SceneOpsBaseModel):
    jobs: list[StatusCount]
    pipelines: list[StatusCount]
    recent_failures: list[RecentFailureItem]
