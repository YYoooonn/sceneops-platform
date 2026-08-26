from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from .enums import JobType


class CreateJobRequest(SceneOpsBaseModel):
    type: JobType

    dataset_id: str | None = None
    dataset_version: str | None = None

    params: JsonDict = Field(default_factory=dict)

    pipeline_run_id: str | None = None
    pipeline_task_run_id: str | None = None
    pipeline_task_id: str | None = None

    max_retries: int = 0

    # If a job with the same computed execution_key already exists as
    # QUEUED/RUNNING/SUCCEEDED, that job is returned instead of creating a
    # duplicate. Set force=True to always create a new job.
    force: bool = False

    metadata: JsonDict = Field(default_factory=dict)
