from __future__ import annotations

from typing import Any, Protocol

from sceneops_core.jobs.schemas import JobManifest
from sceneops_core.jobs.schemas import (
    JobEventLevel,
    JobEventManifest,
    JobEventType,
)
from sceneops_db.jobs import PostgresJobRepository
from sceneops_db.session import async_session_scope
from sceneops_db.jobs import PostgresJobEventRepository


class JobEventStore(Protocol):
    async def append(
        self,
        *,
        job_id: str,
        event_type: JobEventType,
        level: JobEventLevel = JobEventLevel.INFO,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobEventManifest: ...


class JobStore(Protocol):
    async def get_job(self, job_id: str) -> JobManifest | None: ...

    async def create_job(self, job: JobManifest) -> JobManifest: ...

    async def save_job(self, job: JobManifest) -> JobManifest: ...


class JobRegistryStore:
    async def create_job(self, job: JobManifest) -> JobManifest:
        async with async_session_scope() as session:
            repository = PostgresJobRepository(session)
            return await repository.create(job)

    async def get_job(self, job_id: str) -> JobManifest | None:
        async with async_session_scope() as session:
            repository = PostgresJobRepository(session)

            try:
                return await repository.get(job_id)
            except FileNotFoundError:
                return None

    async def save_job(self, job: JobManifest) -> JobManifest:
        async with async_session_scope() as session:
            repository = PostgresJobRepository(session)
            return await repository.update(job)


class JobEventRegistryStore:
    async def append(
        self,
        *,
        job_id: str,
        event_type: JobEventType,
        level: JobEventLevel = JobEventLevel.INFO,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobEventManifest:
        async with async_session_scope() as session:
            repository = PostgresJobEventRepository(session)
            return await repository.append(
                job_id=job_id,
                event_type=event_type,
                level=level,
                message=message,
                payload=payload,
            )
