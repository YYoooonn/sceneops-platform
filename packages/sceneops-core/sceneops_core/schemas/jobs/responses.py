from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.jobs.events import JobEventManifest
from sceneops_core.schemas.jobs.manifests import JobManifest


class JobListResponse(SceneOpsBaseModel):
    jobs: list[JobManifest]
    count: int


class JobEventListResponse(SceneOpsBaseModel):
    events: list[JobEventManifest]
    count: int
