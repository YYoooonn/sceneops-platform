from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from .enums import JobType


class CreateJobRequest(SceneOpsBaseModel):
    """An atomic Job. A Job that belongs to a pipeline task is created by the
    pipeline's orchestrator only, so this request carries no pipeline linkage."""

    type: JobType

    dataset_id: str | None = None
    dataset_version: str | None = None

    params: JsonDict = Field(default_factory=dict)

    max_retries: int = 0

    # If a job with the same computed execution_key already exists as
    # QUEUED/RUNNING/SUCCEEDED, that job is returned instead of creating a
    # duplicate. Set force=True to always create a new job.
    force: bool = False

    metadata: JsonDict = Field(default_factory=dict)
