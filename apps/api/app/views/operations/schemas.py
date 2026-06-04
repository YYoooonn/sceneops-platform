from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_core.jobs.schemas import JobManifest
from sceneops_core.pipelines.schemas import PipelineRunManifest


class OperationCountSummary(SceneOpsBaseModel):
    """Approximate counts from recent records (not global DB aggregates)."""

    running: int = 0
    failed: int = 0
    succeeded: int = 0
    pending: int = 0


class OperationSummaryResponse(SceneOpsBaseModel):
    jobs: OperationCountSummary
    pipelines: OperationCountSummary
    executions: OperationCountSummary


class OperationTimelineEvent(SceneOpsBaseModel):
    event_type: str
    resource_type: str
    resource_id: str
    status: str | None = None
    message: str | None = None
    created_at: datetime | None = None
    metadata: JsonDict = Field(default_factory=dict)


class OperationTimelineResponse(SceneOpsBaseModel):
    events: list[OperationTimelineEvent]
    count: int


class RecentJobsResponse(SceneOpsBaseModel):
    jobs: list[JobManifest]
    count: int


class RecentPipelinesResponse(SceneOpsBaseModel):
    pipeline_runs: list[PipelineRunManifest]
    count: int


class RecentExecutionsResponse(SceneOpsBaseModel):
    executions: list[ExecutionDispatchResult]
    count: int


class OperationFailure(SceneOpsBaseModel):
    resource_type: str
    resource_id: str
    status: str
    error: JsonDict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OperationFailuresResponse(SceneOpsBaseModel):
    failures: list[OperationFailure]
    count: int
