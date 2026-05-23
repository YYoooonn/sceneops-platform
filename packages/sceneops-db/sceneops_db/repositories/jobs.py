from __future__ import annotations

from typing import Any, Protocol

from sceneops_core.schemas.jobs import JobManifest, JobStatus


class JobRepository(Protocol):
    async def create(self, manifest: JobManifest) -> JobManifest:
        ...

    async def get(self, job_id: str) -> JobManifest:
        ...

    async def list(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[JobManifest]:
        ...

    async def update(self, manifest: JobManifest) -> JobManifest:
        ...

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> JobManifest:
        ...
