from __future__ import annotations

from typing import Any, Protocol

from sceneops_core.jobs.schemas import (
    JobEventLevel,
    JobEventManifest,
    JobEventType,
)
from sceneops_db.jobs import PostgresJobEventRepository
from sceneops_db.session import async_session_scope


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


class PostgresJobEventStore:
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
