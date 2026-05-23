from __future__ import annotations

from pydantic import BaseModel

from sceneops_core.schemas.jobs.events import JobEventManifest
from sceneops_core.schemas.jobs.manifests import JobManifest


class JobListResponse(BaseModel):
    jobs: list[JobManifest]
    count: int


class JobEventListResponse(BaseModel):
    events: list[JobEventManifest]
    count: int
