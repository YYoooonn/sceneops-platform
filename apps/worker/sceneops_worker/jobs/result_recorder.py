from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobStepStatus


class JobResultRecorder:
    def to_payload(self, result: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")

        return result

    def mark_job_running(
        self,
        job: JobManifest,
        *,
        worker_id: str | None,
    ) -> None:
        now = utc_now()

        job.status = JobStatus.RUNNING
        job.worker_id = worker_id
        job.locked_at = now
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.updated_at = now
        job.finished_at = None
        job.error = None

        self.mark_first_pending_step_running(job)

    def mark_job_succeeded(
        self,
        job: JobManifest,
        *,
        result: dict[str, Any],
    ) -> None:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                step.status = JobStepStatus.SUCCEEDED
                step.started_at = step.started_at or job.started_at
                step.finished_at = step.finished_at or now
                break

        job.status = JobStatus.SUCCEEDED
        job.result = result
        job.error = None
        job.heartbeat_at = now
        job.finished_at = now
        job.updated_at = now

    def mark_job_failed(
        self,
        job: JobManifest,
        *,
        error: ErrorInfo,
    ) -> None:
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

    def mark_first_pending_step_running(self, job: JobManifest) -> None:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.PENDING:
                step.status = JobStepStatus.RUNNING
                step.started_at = step.started_at or now
                return

    def get_running_step(self, job: JobManifest) -> tuple[str, str] | None:
        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                return step.job_step_id, step.job_step_name

        return None
