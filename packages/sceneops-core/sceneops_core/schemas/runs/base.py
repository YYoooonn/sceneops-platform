from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import ErrorInfo, JsonDict
from sceneops_core.schemas.runs.enums import RunStatus


class BaseRunRecord(SceneOpsBaseModel):
    id: str
    status: RunStatus = RunStatus.PENDING

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    job_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
    error: ErrorInfo | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
