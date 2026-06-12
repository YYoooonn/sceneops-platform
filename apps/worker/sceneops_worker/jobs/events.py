from __future__ import annotations

from typing import Any

from sceneops_core.common.ids import generate_job_event_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobStepStatus,
)


class JobEventPublisher:
    def __init__(self, event_store, *, worker_id: str | None) -> None:
        self.event_store = event_store
        self.worker_id = worker_id

    async def job_locked(self, job: JobManifest) -> None:
        await self._append(
            job=job,
            event_type=JobEventType.LOCKED,
            status=JobStatus.RUNNING,
            message="Job locked",
            data={"worker_id": self.worker_id, "job_type": job.type.value},
        )

    async def job_started(self, job: JobManifest) -> None:
        await self._append(
            job=job,
            event_type=JobEventType.STARTED,
            status=JobStatus.RUNNING,
            message="Job started",
            data={"worker_id": self.worker_id, "job_type": job.type.value},
        )

    async def step_started(
        self,
        job: JobManifest,
        *,
        step_id: str | None,
        step_name: str | None,
    ) -> None:
        if step_id is None:
            return

        await self._append(
            job=job,
            event_type=JobEventType.STEP_STARTED,
            job_step_id=step_id,
            job_step_name=step_name,
            job_step_status=JobStepStatus.RUNNING,
            message=f"Step started: {step_name}",
        )

    async def step_succeeded(
        self,
        job: JobManifest,
        *,
        step_id: str | None,
        step_name: str | None,
    ) -> None:
        if step_id is None:
            return

        await self._append(
            job=job,
            event_type=JobEventType.STEP_SUCCEEDED,
            job_step_id=step_id,
            job_step_name=step_name,
            job_step_status=JobStepStatus.SUCCEEDED,
            message=f"Step succeeded: {step_name}",
        )

    async def job_succeeded(self, job: JobManifest) -> None:
        await self._append(
            job=job,
            event_type=JobEventType.SUCCEEDED,
            status=JobStatus.SUCCEEDED,
            message="Job succeeded",
        )

    async def step_failed(
        self,
        job: JobManifest,
        *,
        step_id: str | None,
        step_name: str | None,
        error: ErrorInfo,
    ) -> None:
        if step_id is None:
            return

        await self._append(
            job=job,
            event_type=JobEventType.STEP_FAILED,
            level=JobEventLevel.ERROR,
            job_step_id=step_id,
            job_step_name=step_name,
            job_step_status=JobStepStatus.FAILED,
            message=f"Step failed: {step_name}",
            error=error,
        )

    async def job_failed(self, job: JobManifest, *, error: ErrorInfo) -> None:
        await self._append(
            job=job,
            event_type=JobEventType.FAILED,
            level=JobEventLevel.ERROR,
            status=JobStatus.FAILED,
            message="Job failed",
            error=error,
        )

    async def _append(
        self,
        *,
        job: JobManifest,
        event_type: JobEventType,
        level: JobEventLevel = JobEventLevel.INFO,
        status: JobStatus | None = None,
        job_step_id: str | None = None,
        job_step_name: str | None = None,
        job_step_status: JobStepStatus | None = None,
        message: str | None = None,
        error: ErrorInfo | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = JobEvent(
            event_id=generate_job_event_id(),
            job_id=job.job_id,
            type=event_type,
            job_type=job.type,
            level=level,
            status=status,
            job_step_id=job_step_id,
            job_step_name=job_step_name,
            job_step_status=job_step_status,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_task_run_id=job.pipeline_task_run_id,
            pipeline_task_id=job.pipeline_task_id,
            worker_id=self.worker_id,
            message=message,
            error=error,
            data=data or {},
            created_at=utc_now(),
        )
        await self.event_store.append(event)
