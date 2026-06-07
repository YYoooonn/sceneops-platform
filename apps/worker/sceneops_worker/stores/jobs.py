from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_db.postgres import PostgresJobEventRepository, PostgresJobRepository


class JobStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresJobRepository(session)

    async def get(self, job_id: str) -> JobManifest | None:
        return await self._repo.get(job_id)

    async def create(self, job: JobManifest) -> JobManifest:
        return await self._repo.create(job)

    async def save(self, job: JobManifest) -> JobManifest:
        return await self._repo.update(job)

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
    ) -> list[JobManifest]:
        return await self._repo.list(
            type=type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            pipeline_run_id=pipeline_run_id,
            pipeline_task_run_id=pipeline_task_run_id,
            limit=limit,
            offset=offset,
        )


class JobEventStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresJobEventRepository(session)

    async def append(self, event: JobEvent) -> JobEvent:
        return await self._repo.append(event)

    async def list_for_job(
        self,
        job_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]:
        return await self._repo.list_for_job(
            job_id,
            level=level,
            type=type,
            limit=limit,
            offset=offset,
        )

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]:
        return await self._repo.list_for_pipeline_run(
            pipeline_run_id,
            level=level,
            type=type,
            limit=limit,
            offset=offset,
        )
