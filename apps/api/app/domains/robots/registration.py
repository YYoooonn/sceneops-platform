from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobManifest,
    JobStatus,
    JobType,
)

from app.domains.robots.schemas import RegisterRobotRunResponse
from app.platform.jobs.dispatch_facade import JobDispatchFacade
from app.platform.jobs.service import JobDispatchConflictError, JobService


class RegistrationDispatchError(RuntimeError):
    """The registration Job was created and committed but handing it to the
    execution backend failed (broker unreachable, ...). The Job is *not* rolled
    back: it stays PENDING or QUEUED in PostgreSQL, where reconciliation finds
    it and recovers it as a stalled Job (ADR-008 §3.2, W7). ``job`` is that
    Job; the original error is ``__cause__``."""

    def __init__(self, job: JobManifest) -> None:
        super().__init__(
            f"REGISTER_ROBOT_RUN job {job.job_id} was created but could not be "
            f"dispatched (status={job.status.value})"
        )
        self.job = job


class RobotRunRegistrationService:
    """Submits REGISTER_ROBOT_RUN through the normal Job mechanism. Holds no
    registration logic: verification and the DB projection happen in the
    worker's job handler.

    The Job is committed in its own session before dispatch, matching
    JobDispatchFacade's commit-before-dispatch rule, so a worker can never
    claim a job whose creation has not committed yet."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        job_service_factory: Callable[[AsyncSession], JobService],
        dispatch_facade: JobDispatchFacade,
    ) -> None:
        self._session_factory = session_factory
        self._job_service_factory = job_service_factory
        self._dispatch_facade = dispatch_facade

    async def submit(
        self, manifest_uri: str, *, force: bool = False
    ) -> RegisterRobotRunResponse:
        """``force`` bypasses execution-key deduplication so a Job is always
        created. Only the reconciler's stalled-Job replacement uses it (after
        abandoning the stalled Job); the HTTP route never does."""
        async with self._session_factory() as session:
            job = await self._job_service_factory(session).create_job(
                CreateJobRequest(
                    type=JobType.REGISTER_ROBOT_RUN,
                    params={"manifest_uri": manifest_uri},
                    force=force,
                )
            )
            await session.commit()

        # create_job may return an existing equivalent job (execution-key
        # dedup); only a job that was never dispatched is dispatched here.
        execution = None
        if job.status == JobStatus.PENDING:
            try:
                execution = await self._dispatch_facade.dispatch(
                    job.job_id, expected_status=JobStatus.PENDING
                )
            except JobDispatchConflictError:
                # A concurrent submission of the same manifest dispatched this
                # Job first (or it has moved on since): it is not sent twice.
                job = await self._current(job)
            except Exception as exc:
                raise RegistrationDispatchError(job) from exc
        return RegisterRobotRunResponse(job=job, execution=execution)

    async def _current(self, job: JobManifest) -> JobManifest:
        async with self._session_factory() as session:
            current = await self._job_service_factory(session).get_job(job.job_id)
        return current or job
