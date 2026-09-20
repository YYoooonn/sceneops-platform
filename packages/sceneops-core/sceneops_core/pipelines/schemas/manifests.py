from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import ErrorInfo, JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import JobType

from .enums import PipelineRunStatus, PipelineTaskRunStatus, PipelineType
from .results import PipelineRunResult, PipelineTaskResult


class PipelineRunManifest(SceneOpsBaseModel):
    pipeline_run_id: str
    type: PipelineType
    status: PipelineRunStatus

    dataset_id: str | None = None
    dataset_version: str | None = None

    model_id: str | None = None
    model_version: str | None = None

    params: JsonDict = Field(default_factory=dict)

    result: PipelineRunResult | None = None
    error: ErrorInfo | None = None

    execution_key: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class PipelineTaskRunManifest(SceneOpsBaseModel):
    pipeline_task_run_id: str
    pipeline_run_id: str

    pipeline_task_id: str
    pipeline_task_name: str
    task_order: int

    status: PipelineTaskRunStatus

    job_type: JobType
    job_id: str | None = None

    depends_on_task_ids: list[str] = Field(default_factory=list)

    params: JsonDict = Field(default_factory=dict)

    result: PipelineTaskResult | None = None
    error: ErrorInfo | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
