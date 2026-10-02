from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.jobs.schemas import CreateJobRequest, JobStatus, JobType

from app.domains.robots.schemas import RegisterRobotRunResponse
from app.platform.jobs.dispatch_facade import JobDispatchFacade
from app.platform.jobs.service import JobService


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

    async def submit(self, manifest_uri: str) -> RegisterRobotRunResponse:
        async with self._session_factory() as session:
            job = await self._job_service_factory(session).create_job(
                CreateJobRequest(
                    type=JobType.REGISTER_ROBOT_RUN,
                    params={"manifest_uri": manifest_uri},
                )
            )
            await session.commit()

        # create_job may return an existing equivalent job (execution-key
        # dedup); only a job that was never dispatched is dispatched here.
        execution = None
        if job.status == JobStatus.PENDING:
            execution = await self._dispatch_facade.dispatch(job.job_id)
        return RegisterRobotRunResponse(job=job, execution=execution)
