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

    # If a job with the same computed execution_key is PENDING / QUEUED /
    # RUNNING / SUCCEEDED, that job is returned instead of creating a duplicate.
    # force=True creates a new job even after a succeeded one; a job that is
    # still pending, queued or running is returned in either case.
    force: bool = False

    metadata: JsonDict = Field(default_factory=dict)
