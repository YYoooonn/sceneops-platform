from __future__ import annotations

from datetime import datetime
from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict, ErrorInfo
from sceneops_core.jobs.schemas import JobType
from .enums import (
    PipelineRunStatus,
    PipelineStepRunStatus,
    PipelineType,
)
from .results import PipelineRunResult, PipelineStepResult


class PipelineRunManifest(SceneOpsBaseModel):
    pipeline_run_id: str
    type: PipelineType
    status: PipelineRunStatus

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None

    params: JsonDict = Field(default_factory=dict)
    result: PipelineRunResult | None = None
    error: ErrorInfo | None = None

    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PipelineStepRunManifest(SceneOpsBaseModel):
    pipeline_step_run_id: str
    pipeline_run_id: str

    step_name: str
    step_order: int
    status: PipelineStepRunStatus

    job_type: JobType
    job_id: str | None = None

    depends_on_step_names: list[str] = Field(default_factory=list)

    params: JsonDict = Field(default_factory=dict)
    result: PipelineStepResult | None = None
    error: ErrorInfo | None = None

    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
