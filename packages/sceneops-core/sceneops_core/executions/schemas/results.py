from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import ExecutionBackend, ExecutionKind


class ExecutionDispatchResult(SceneOpsBaseModel):
    """The audit record of one message sent to an execution backend: which
    resource (a Job to run, or a PipelineRun to advance) went where, and the
    backend's id for the message. It records the send and nothing after it; the
    state of the work is the Job's and the PipelineRun's own."""

    execution_id: str
    execution_backend: ExecutionBackend
    execution_kind: ExecutionKind
    resource_id: str

    external_id: str | None = Field(default=None)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
