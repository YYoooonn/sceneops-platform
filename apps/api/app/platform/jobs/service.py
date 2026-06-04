from __future__ import annotations

from sceneops_core.common.ids import generate_job_event_id, generate_job_id
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobEvent,
    JobEventLevel,
    JobEventListResponse,
    JobEventType,
    JobListResponse,
    JobManifest,
    JobStatus,
    create_initial_job_steps,
    parse_job_params,
)
from sceneops_db.repositories.jobs import JobEventRepository, JobRepository


class JobService:
    def __init__(
        self,
        *,
        repository: JobRepository,
        event_repository: JobEventRepository,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self._repository = repository
        self._event_repository = event_repository
        self._default_dataset_id = default_dataset_id
        self._default_dataset_version = default_dataset_version

    async def create_job(self, request: CreateJobRequest) -> JobManifest:
        now = utc_now()

        dataset_id = request.dataset_id or self._default_dataset_id
        dataset_version = request.dataset_version or self._default_dataset_version

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
            pipeline_step_id=request.pipeline_step_id,
            params=validated_params.model_dump(),
            steps=create_initial_job_steps(request.type),
            retry_count=0,
            max_retries=request.max_retries,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )

        created = await self._repository.create(job)

        await self._event_repository.append(
            JobEvent(
                event_id=generate_job_event_id(),
                job_id=created.job_id,
                type=JobEventType.CREATED,
                level=JobEventLevel.INFO,
                job_type=created.type,
                pipeline_run_id=created.pipeline_run_id,
                pipeline_step_run_id=created.pipeline_step_run_id,
                pipeline_step_id=created.pipeline_step_id,
                message="Job created",
                data={
                    "dataset_id": created.dataset_id,
                    "dataset_version": created.dataset_version,
                },
                created_at=now,
            )
        )

        return created

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JobListResponse:
        jobs = await self._repository.list(
            type=job_type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            limit=limit,
            offset=offset,
        )
        return JobListResponse(jobs=jobs, count=len(jobs))

    async def get_job(self, job_id: str) -> JobManifest | None:
        return await self._repository.get(job_id)

    async def list_job_events(self, job_id: str) -> JobEventListResponse | None:
        job = await self._repository.get(job_id)
        if job is None:
            return None
        events = await self._event_repository.list_for_job(job_id)
        return JobEventListResponse(events=events, count=len(events))

    async def validate_executable(self, job_id: str) -> JobManifest:
        job = await self._repository.get(job_id)
        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        blocked = {JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.CANCELLED}
        if job.status in blocked:
            raise ValueError(
                f"Job is not executable: job_id={job_id}, status={job.status}"
            )
        return job
