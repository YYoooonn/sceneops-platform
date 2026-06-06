from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_bad_request, raise_not_found
from app.core.pagination import PaginationDep
from app.platform.executions.dependencies import ExecutionServiceDep
from app.platform.jobs.dependencies import JobServiceDep
from app.platform.jobs.schemas import (
    JobDetailResponse,
    JobEventListResponse,
    JobExecuteResponse,
    JobListResponse,
)
from sceneops_core.jobs.schemas import CreateJobRequest, JobStatus

router = APIRouter()


@router.post(
    "", response_model=JobDetailResponse, status_code=201, summary="Create job"
)
async def create_job(
    request: CreateJobRequest,
    service: JobServiceDep,
) -> JobDetailResponse:
    job = await service.create_job(request)
    return JobDetailResponse(job=job)


@router.get("", response_model=JobListResponse, summary="List jobs")
async def list_jobs(
    *,
    service: JobServiceDep,
    pagination: PaginationDep,
    status: JobStatus | None = None,
    job_type: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
) -> JobListResponse:
    return await service.list_jobs(
        status=status,
        job_type=job_type,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{job_id}", response_model=JobDetailResponse, summary="Get job")
async def get_job(job_id: str, service: JobServiceDep) -> JobDetailResponse:
    job = await service.get_job(job_id)
    if job is None:
        raise_not_found("Job", job_id)
    return JobDetailResponse(job=job)


@router.get(
    "/{job_id}/events", response_model=JobEventListResponse, summary="List job events"
)
async def list_job_events(job_id: str, service: JobServiceDep) -> JobEventListResponse:
    result = await service.list_job_events(job_id)
    if result is None:
        raise_not_found("Job", job_id)
    return result


@router.post(
    "/{job_id}/execute",
    response_model=JobExecuteResponse,
    summary="Dispatch job execution",
)
async def execute_job(
    job_id: str,
    service: JobServiceDep,
    execution_service: ExecutionServiceDep,
) -> JobExecuteResponse:
    try:
        execution = await service.dispatch_job(job_id, execution_service)
    except FileNotFoundError:
        raise_not_found("Job", job_id)
    except ValueError as exc:
        raise_bad_request(str(exc))
    return JobExecuteResponse(execution=execution)
