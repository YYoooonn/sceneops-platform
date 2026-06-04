from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .events import JobEvent
from .manifests import JobManifest


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
