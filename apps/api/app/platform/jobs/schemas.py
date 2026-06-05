from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_core.jobs.schemas.events import JobEvent
from sceneops_core.jobs.schemas.manifests import JobManifest


class JobDetailResponse(SceneOpsBaseModel):
    job: JobManifest
    events: list[JobEvent] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class JobListResponse(SceneOpsBaseModel):
    jobs: list[JobManifest]
    count: int


class JobEventListResponse(SceneOpsBaseModel):
    events: list[JobEvent]
    count: int


class JobExecuteResponse(SceneOpsBaseModel):
    execution: ExecutionDispatchResult
