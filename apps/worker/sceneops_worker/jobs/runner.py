from __future__ import annotations

from typing import Any

from sceneops_core.schemas.jobs import (
    JobManifest,
    JobStatus,
    JobStepStatus,
)
from sceneops_core.time import utc_now_iso

from sceneops_worker.jobs.executors import JobExecutor
from sceneops_worker.jobs.store import JobStore


class JobRunner:
    def __init__(
        self,
        *,
        job_store: JobStore,
        job_executor: JobExecutor,
    ) -> None:
        self.job_store = job_store
        self.job_executor = job_executor

    async def run(self, job_id: str) -> JobManifest:
        job = await self.job_store.get_job(job_id)

        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        self._validate_runnable(job)

        job = await self._mark_job_running(job)

        try:
            result = self.job_executor.execute(job)
            job = await self._mark_job_succeeded(job, result=result)
            return job

        except Exception as error:
            await self._mark_job_failed(
                job,
                error={
                    "type": error.__class__.__name__,
                    "message": str(error),
                },
            )
            raise

    def _validate_runnable(self, job: JobManifest) -> None:
        if job.status == JobStatus.SUCCEEDED:
            raise RuntimeError(f"Job is already succeeded: {job.jobId}")

        if job.status == JobStatus.RUNNING:
            raise RuntimeError(f"Job is already running: {job.jobId}")

        if job.status == JobStatus.CANCELED:
            raise RuntimeError(f"Job is canceled: {job.jobId}")

    async def _mark_job_running(self, job: JobManifest) -> JobManifest:
        now = utc_now_iso()

        job.status = JobStatus.RUNNING
        job.startedAt = job.startedAt or now
        job.updatedAt = now
        job.finishedAt = None
        job.error = None

        self._mark_first_pending_step_running(job)

        return await self.job_store.save_job(job)

    async def _mark_job_succeeded(
        self,
        job: JobManifest,
        *,
        result: dict[str, Any],
    ) -> JobManifest:
        now = utc_now_iso()

        for step in job.steps:
            if step.status in {JobStepStatus.PENDING, JobStepStatus.RUNNING}:
                step.status = JobStepStatus.SUCCEEDED
                step.startedAt = step.startedAt or job.startedAt or now
                step.finishedAt = step.finishedAt or now

        job.status = JobStatus.SUCCEEDED
        job.result = result
        job.error = None
        job.finishedAt = now
        job.updatedAt = now

        return await self.job_store.save_job(job)

    async def _mark_job_failed(
        self,
        job: JobManifest,
        *,
        error: dict[str, Any],
    ) -> JobManifest:
        now = utc_now_iso()

        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                step.status = JobStepStatus.FAILED
                step.finishedAt = now

        job.status = JobStatus.FAILED
        job.error = error
        job.finishedAt = now
        job.updatedAt = now

        return await self.job_store.save_job(job)

    def _mark_first_pending_step_running(self, job: JobManifest) -> None:
        now = utc_now_iso()

        for step in job.steps:
            if step.status == JobStepStatus.PENDING:
                step.status = JobStepStatus.RUNNING
                step.startedAt = step.startedAt or now
                return
