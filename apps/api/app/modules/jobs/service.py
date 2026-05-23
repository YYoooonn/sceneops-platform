from sceneops_core.ids.jobs import generate_job_id
from sceneops_core.schemas.jobs import (
    CreateJobRequest,
    JobListResponse,
    JobManifest,
    JobStatus,
    build_default_steps,
)
from sceneops_core.time import utc_now_iso
from sceneops_db.repositories import JobRepository


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

    async def create_job(self, request: CreateJobRequest) -> JobManifest:
        now = utc_now_iso()

        dataset_id = request.datasetId or self.default_dataset_id
        dataset_version = request.datasetVersion or self.default_dataset_version

        job = JobManifest(
            jobId=generate_job_id(),
            type=request.type,
            status=JobStatus.PENDING,
            datasetId=dataset_id,
            datasetVersion=dataset_version,
            params=request.params,
            steps=build_default_steps(request.type),
            retryCount=0,
            maxRetries=0,  # XXX for now
            queuedAt=now,
            createdAt=now,
            updatedAt=now,
        )

        return await self.repository.create(job)

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> JobListResponse:
        jobs = await self.repository.list(
            status=status,
            job_type=job_type,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )

        return JobListResponse(
            jobs=jobs,
            count=len(jobs),
        )

    async def get_job(self, job_id: str) -> JobManifest | None:
        try:
            return await self.repository.get(job_id)
        except FileNotFoundError:
            return None
