from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Protocol

from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.execution.errors import (
    JobTerminalFailureError,
    JobWaitTimeoutError,
)


_TERMINAL_FAILURE_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.SKIPPED}
)


class JobWatcher(Protocol):
    async def wait_until_terminal(self, job_id: str) -> JobManifest: ...


class DbPollingJobWatcher:
    def __init__(
        self,
        context: WorkerContext,
        *,
        poll_interval_seconds: float,
        timeout_seconds: float,
        fail_on_terminal_failure: bool = True,
    ) -> None:
        self._context = context
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._fail_on_terminal_failure = fail_on_terminal_failure

    async def wait_until_terminal(self, job_id: str) -> JobManifest:
        deadline = utc_now() + timedelta(seconds=self._timeout_seconds)

        while True:
            job = await self._context.job_store.get(job_id)

            if job is None:
                raise JobWaitTimeoutError(job_id, self._timeout_seconds)

            if job.status == JobStatus.SUCCEEDED:
                return job

            if job.status in _TERMINAL_FAILURE_STATUSES:
                if self._fail_on_terminal_failure:
                    raise JobTerminalFailureError(job_id, job.status)
                return job

            if utc_now() >= deadline:
                raise JobWaitTimeoutError(job_id, self._timeout_seconds)

            await asyncio.sleep(self._poll_interval_seconds)
