from __future__ import annotations

from typing import Any, Protocol

from sceneops_core.jobs.schemas import (
    JobEventLevel,
    JobEventManifest,
    JobEventType,
    JobManifest,
    JobStatus,
)


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

    async def count_by_status(self) -> dict[str, int]: ...

    async def list_recent_failures(
        self,
        *,
        limit: int = 10,
    ) -> list[JobManifest]: ...


class JobEventRepository(Protocol):
    async def append(
        self,
        *,
        job_id: str,
        event_type: JobEventType,
        level: JobEventLevel = JobEventLevel.INFO,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobEventManifest:
        ...

    async def list_by_job(self, job_id: str) -> list[JobEventManifest]:
        ...
