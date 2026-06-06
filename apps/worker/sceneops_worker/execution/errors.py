from __future__ import annotations

from sceneops_core.jobs.schemas import JobStatus


class JobDispatchError(RuntimeError):
    pass


class JobWaitTimeoutError(RuntimeError):
    def __init__(self, job_id: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Timed out waiting for job {job_id} after {timeout_seconds:.0f}s"
        )
        self.job_id = job_id
        self.timeout_seconds = timeout_seconds


class JobTerminalFailureError(RuntimeError):
    def __init__(self, job_id: str, status: JobStatus) -> None:
        super().__init__(f"Job {job_id} ended in terminal failure: {status}")
        self.job_id = job_id
        self.status = status
