from typing import Protocol

from sceneops_core.schemas.jobs import JobManifest, JobStatus


class JobRepository(Protocol):
    def create_job(self, job: JobManifest) -> JobManifest: ...

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[JobManifest]: ...

    def get_job(self, job_id: str) -> JobManifest | None: ...

    def update_job(self, job: JobManifest) -> JobManifest: ...
