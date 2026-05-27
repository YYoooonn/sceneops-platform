from sceneops_core.ids.jobs import generate_job_id
from sceneops_core.schemas.jobs import (
    CreateJobRequest,
    JobEventListResponse,
    JobEventType,
    JobListResponse,
    JobManifest,
    JobStatus,
    build_default_steps,
    parse_job_params,
)
from sceneops_core.time import utc_now
from sceneops_db.jobs import JobEventRepository, JobRepository


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        event_repository: JobEventRepository,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self.repository = repository
        self.event_repository = event_repository
        self.default_dataset_id = default_dataset_id
        self.default_dataset_version = default_dataset_version

    async def create_job(self, request: CreateJobRequest) -> JobManifest:
        now = utc_now()

        dataset_id = request.dataset_id or self.default_dataset_id
        dataset_version = request.dataset_version or self.default_dataset_version
        raw_params = {
            **request.params,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
        }

        validated_params = parse_job_params(request.type, raw_params)

        job = JobManifest(
            job_id=generate_job_id(),
            type=request.type,
            status=JobStatus.PENDING,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            pipeline_run_id=request.pipeline_run_id,
            pipeline_step_run_id=request.pipeline_step_run_id,
            pipeline_step_name=request.pipeline_step_name,
            params=validated_params.to_db_dict(),
            steps=build_default_steps(request.type),
            retry_count=0,
            max_retries=request.max_retries,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )

        created = await self.repository.create(job)

        await self.event_repository.append(
            job_id=created.job_id,
            event_type=JobEventType.JOB_CREATED,
            message="Job created",
            payload={
                "job_type": str(created.type),
                "dataset_id": created.dataset_id,
                "dataset_version": created.dataset_version,
                "pipeline_run_id": created.pipeline_run_id,
                "pipeline_step_run_id": created.pipeline_step_run_id,
                "pipeline_step_name": created.pipeline_step_name,
            },
        )

        return created

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

    async def list_job_events(self, job_id: str) -> JobEventListResponse | None:
        job = await self.get_job(job_id)

        if job is None:
            return None

        events = await self.event_repository.list_by_job(job_id)

        return JobEventListResponse(
            events=events,
            count=len(events),
        )
