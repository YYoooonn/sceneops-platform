from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.common.schemas import JsonDict
from .enums import JobType


class CreateJobRequest(SceneOpsBaseModel):
    type: JobType

    dataset_id: str | None = None
    dataset_version: str | None = None

    params: JsonDict = Field(default_factory=dict)

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    pipeline_step_name: str | None = None

    max_retries: int = 0
