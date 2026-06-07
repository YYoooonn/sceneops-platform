from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from celery import Celery

from sceneops_core.constants.tasks import JOB_RUN_TASK

from sceneops_worker.execution.errors import JobDispatchError


class JobDispatchBackend(Protocol):
    async def dispatch_job(self, job_id: str) -> None: ...


@dataclass(frozen=True)
class CeleryJobDispatchBackend:
    app: Celery
    jobs_queue: str

    async def dispatch_job(self, job_id: str) -> None:
        try:
            self.app.send_task(
                JOB_RUN_TASK,
                args=[job_id],
                queue=self.jobs_queue,
                routing_key=self.jobs_queue,
            )
        except Exception as exc:
            raise JobDispatchError(
                f"Failed to dispatch job {job_id} to queue {self.jobs_queue!r}"
            ) from exc
