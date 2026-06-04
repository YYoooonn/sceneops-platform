from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import ErrorInfo, JsonDict, SceneOpsBaseModel

from .enums import RunStatus, RunType


class BaseRunRecord(SceneOpsBaseModel):
    run_id: str
    type: RunType
    status: RunStatus = RunStatus.PENDING

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    job_id: str | None = None

    params: JsonDict = Field(default_factory=dict)
    result: JsonDict | None = None
    error: ErrorInfo | None = None

    artifact_root_uri: str | None = None
    manifest_uri: str | None = None

    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
