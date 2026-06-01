from __future__ import annotations

from typing import Any

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import (
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobStepStatus,
)
from sceneops_core.time import utc_now

from sceneops_worker.jobs.executor import JobExecutor
from sceneops_worker.jobs.store import JobStore
from sceneops_worker.jobs.event_store import JobEventStore


class JobRunner:
    def __init__(
        self,
        *,
        job_store: JobStore,
        job_executor: JobExecutor,
        job_event_store: JobEventStore,
        worker_id: str,
    ) -> None:
        self.job_store = job_store
        self.job_event_store = job_event_store
        self.job_executor = job_executor
        self.worker_id = worker_id

    async def run(self, job_id: str) -> JobManifest:
        job = await self.job_store.get_job(job_id)

        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        self._validate_runnable(job)

        job = await self._mark_job_running(job)

        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.JOB_STARTED,
            message="Job started",
            payload={
                "worker_id": self.worker_id,
                "job_type": job.type.value
                if hasattr(job.type, "value")
                else str(job.type),
            },
        )

        running_step_name = self._get_running_step_name(job)

        try:
            if running_step_name is not None:
                await self.job_event_store.append(
                    job_id=job.job_id,
                    event_type=JobEventType.STEP_STARTED,
                    message=f"Step started: {running_step_name}",
                    payload={"step": running_step_name},
                )

            result = await self.job_executor.execute(job)

            if running_step_name is not None:
                await self.job_event_store.append(
                    job_id=job.job_id,
                    event_type=JobEventType.STEP_SUCCEEDED,
                    message=f"Step succeeded: {running_step_name}",
                    payload={"step": running_step_name},
                )

            job = await self._mark_job_succeeded(job, result=result)

            await self.job_event_store.append(
                job_id=job.job_id,
                event_type=JobEventType.JOB_SUCCEEDED,
                message="Job succeeded",
                payload={"result": result},
            )

            return job

        except Exception as error:
            if running_step_name is not None:
                await self.job_event_store.append(
                    job_id=job.job_id,
                    event_type=JobEventType.STEP_FAILED,
                    level=JobEventLevel.ERROR,
                    message=f"Step failed: {running_step_name}",
                    payload={
                        "step": running_step_name,
                        "errorType": error.__class__.__name__,
                        "errorMessage": str(error),
                    },
                )

            await self._mark_job_failed(
                job,
                error=ErrorInfo(type=error.__class__.__name__, message=str(error)),
            )

            await self.job_event_store.append(
                job_id=job.job_id,
                event_type=JobEventType.JOB_FAILED,
                level=JobEventLevel.ERROR,
                message="Job failed",
                payload={
                    "errorType": error.__class__.__name__,
                    "errorMessage": str(error),
                },
            )

            raise

    def _validate_runnable(self, job: JobManifest) -> None:
        if job.status == JobStatus.SUCCEEDED:
            raise RuntimeError(f"Job is already succeeded: {job.job_id}")

        if job.status == JobStatus.RUNNING:
            raise RuntimeError(f"Job is already running: {job.job_id}")

        if job.status == JobStatus.CANCELED:
            raise RuntimeError(f"Job is canceled: {job.job_id}")

    async def _mark_job_running(self, job: JobManifest) -> JobManifest:
        now = utc_now()

        job.status = JobStatus.RUNNING
        job.worker_id = self.worker_id
        job.locked_at = now
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.updated_at = now
        job.finished_at = None
        job.error = None

        self._mark_first_pending_step_running(job)

        return await self.job_store.save_job(job)

    async def _mark_job_succeeded(
        self,
        job: JobManifest,
        *,
        result: dict[str, Any],
    ) -> JobManifest:
        now = utc_now()

        for step in job.steps:
            if step.status in {JobStepStatus.PENDING, JobStepStatus.RUNNING}:
                step.status = JobStepStatus.SUCCEEDED
                step.started_at = step.started_at or job.started_at or now
                step.finished_at = step.finished_at or now

        job.status = JobStatus.SUCCEEDED
        job.result = result
        job.error = None
        job.heartbeat_at = now
        job.finished_at = now
        job.updated_at = now

        return await self.job_store.save_job(job)

    async def _mark_job_failed(
        self,
        job: JobManifest,
        *,
        error: ErrorInfo,
    ) -> JobManifest:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                step.status = JobStepStatus.FAILED
                step.finished_at = now

        job.status = JobStatus.FAILED
        job.error = error
        job.heartbeat_at = now
        job.finished_at = now
        job.updated_at = now

        return await self.job_store.save_job(job)

    def _mark_first_pending_step_running(self, job: JobManifest) -> None:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.PENDING:
                step.status = JobStepStatus.RUNNING
                step.started_at = step.started_at or now
                return

    def _get_running_step_name(self, job: JobManifest) -> str | None:
        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                if hasattr(step, "name"):
                    return step.name
                return "unknown"
        return None
