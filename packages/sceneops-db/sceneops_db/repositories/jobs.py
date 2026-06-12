from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
)


@runtime_checkable
class JobRepository(Protocol):
    async def create(self, job: JobManifest) -> JobManifest: ...

    async def get(self, job_id: str) -> JobManifest | None: ...

    async def update(self, job: JobManifest) -> JobManifest: ...

    async def list(
        self,
        *,
        type: JobType | None = None,
        status: JobStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        pipeline_run_id: str | None = None,
        pipeline_task_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobManifest]: ...

    async def count_by_status(self) -> dict[str, int]: ...


@runtime_checkable
class JobEventRepository(Protocol):
    async def append(self, event: JobEvent) -> JobEvent: ...

    async def get(self, event_id: str) -> JobEvent | None: ...

    async def list_for_job(
        self,
        job_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]: ...

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]: ...
