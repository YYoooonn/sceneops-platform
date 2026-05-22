from uuid import uuid4

from app.modules.jobs.repository import JobRepository
from app.modules.jobs.schemas import (
    CreateJobRequest,
    JobListResponse,
    JobManifest,
    JobStatus,
    utc_now_iso,
)
from app.modules.jobs.template import build_default_steps


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self.repository = repository
        self.default_dataset_id = default_dataset_id
        self.default_dataset_version = default_dataset_version

    def create_job(self, request: CreateJobRequest) -> JobManifest:
        now = utc_now_iso()

        dataset_id = request.datasetId or self.default_dataset_id
        dataset_version = request.datasetVersion or self.default_dataset_version

        job = JobManifest(
            jobId=self._generate_job_id(),
            type=request.type,
            status=JobStatus.PENDING,
            datasetId=dataset_id,
            datasetVersion=dataset_version,
            params=request.params,
            steps=build_default_steps(request.type),
            createdAt=now,
            updatedAt=now,
        )

        return self.repository.create_job(job)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> JobListResponse:
        jobs = self.repository.list_jobs(
            status=status,
            job_type=job_type,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )

        return JobListResponse(
            jobs=jobs,
            count=len(jobs),
        )

    def get_job(self, job_id: str) -> JobManifest | None:
        return self.repository.get_job(job_id)

    def _generate_job_id(self) -> str:
        return f"job-{uuid4().hex[:12]}"
