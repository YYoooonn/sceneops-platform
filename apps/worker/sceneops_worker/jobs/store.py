from __future__ import annotations

from typing import Protocol

from sceneops_core.jobs.schemas import JobManifest
from sceneops_db.jobs import PostgresJobRepository
from sceneops_db.session import async_session_scope


class JobStore(Protocol):
    async def get_job(self, job_id: str) -> JobManifest | None: ...

    async def create_job(self, job: JobManifest) -> JobManifest: ...

    async def save_job(self, job: JobManifest) -> JobManifest: ...


class PostgresJobStore:
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
