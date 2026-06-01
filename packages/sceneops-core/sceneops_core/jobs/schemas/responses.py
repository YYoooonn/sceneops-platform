from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .events import JobEventManifest
from .manifests import JobManifest


class JobListResponse(SceneOpsBaseModel):
    jobs: list[JobManifest]
    count: int


class JobEventListResponse(SceneOpsBaseModel):
    events: list[JobEventManifest]
    count: int
