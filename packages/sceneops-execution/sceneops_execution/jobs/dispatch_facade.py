from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_core.jobs.schemas import JobStatus
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.executions import PostgresExecutionRecordRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository

from sceneops_execution.executions.backends.base import JobExecutionBackend
from sceneops_execution.executions.service import ExecutionService
from sceneops_execution.jobs.service import JobService


class JobDispatchFacade:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        job_backend: JobExecutionBackend,
    ) -> None:
        self._session_factory = session_factory
        self._job_backend = job_backend

    async def dispatch(
        self, job_id: str, *, expected_status: JobStatus | None = None
    ) -> ExecutionDispatchResult:
        # commit-before-backend-dispatch: no worker receives this message before
        # the QUEUED commit. The QUEUED write is conditional on the state it was
        # decided on (JobService.mark_queued), so a dispatch that lost a race to a
        # worker, reconciliation or another dispatch raises
        # JobDispatchConflictError and sends nothing. If backend dispatch fails
        # after commit, the job intentionally remains QUEUED and can be
        # redispatched.
        async with self._session_factory() as session:
            job_service = JobService(
                repository=PostgresJobRepository(session),
                event_repository=PostgresJobEventRepository(session),
                artifact_repository=PostgresArtifactRefRepository(session),
            )
            execution_service = ExecutionService(
                job_backend=self._job_backend,
                record_repository=PostgresExecutionRecordRepository(session),
            )

            await job_service.mark_queued(job_id, expected_status=expected_status)
            await session.commit()

            execution = await execution_service.dispatch_job(job_id)
            await session.commit()

            return execution
