from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_bad_request, raise_not_found
from app.core.pagination import PaginationDep
from app.platform.executions.dependencies import ExecutionServiceDep
from app.platform.jobs.dependencies import JobServiceDep
from app.platform.jobs.schemas import JobExecuteResponse
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobDetailResponse,
    JobEventListResponse,
    JobListResponse,
    JobStatus,
)

router = APIRouter()


@router.post("", response_model=JobDetailResponse, status_code=201)
async def create_job(
    request: CreateJobRequest,
    service: JobServiceDep,
) -> JobDetailResponse:
    job = await service.create_job(request)
    return JobDetailResponse(job=job)


@router.get("", response_model=JobListResponse)
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


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: str, service: JobServiceDep) -> JobDetailResponse:
    job = await service.get_job(job_id)
    if job is None:
        raise_not_found("Job", job_id)
    return JobDetailResponse(job=job)


@router.get("/{job_id}/events", response_model=JobEventListResponse)
async def list_job_events(job_id: str, service: JobServiceDep) -> JobEventListResponse:
    result = await service.list_job_events(job_id)
    if result is None:
        raise_not_found("Job", job_id)
    return result


@router.post("/{job_id}/execute", response_model=JobExecuteResponse)
async def execute_job(
    job_id: str,
    service: JobServiceDep,
    execution_service: ExecutionServiceDep,
) -> JobExecuteResponse:
    try:
        await service.validate_executable(job_id)
    except FileNotFoundError:
        raise_not_found("Job", job_id)
    except ValueError as exc:
        raise_bad_request(str(exc))

    execution = await execution_service.dispatch_job(job_id)
    return JobExecuteResponse(execution=execution)
