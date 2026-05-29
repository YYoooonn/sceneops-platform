from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_execution_dispatcher, get_job_service
from app.modules.executions.dispatchers import ExecutionDispatcher
from app.modules.jobs.schemas import JobExecutionResponse
from app.modules.jobs.service import JobService
from sceneops_core.schemas.executions import ExecutionStatus
from sceneops_core.schemas.jobs import (
    CreateJobRequest,
    JobListResponse,
    JobManifest,
    JobStatus,
    JobEventListResponse,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobManifest)
async def create_job(
    request: CreateJobRequest,
    service: JobService = Depends(get_job_service),
) -> JobManifest:
    return await service.create_job(request)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: JobStatus | None = None,
    job_type: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    service: JobService = Depends(get_job_service),
) -> JobListResponse:
    return await service.list_jobs(
        status=status,
        job_type=job_type,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


@router.get("/{job_id}/events", response_model=JobEventListResponse)
async def list_job_events(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobEventListResponse:
    response = await service.list_job_events(job_id)

    if response is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return response


@router.get("/{job_id}", response_model=JobManifest)
async def get_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobManifest:
    job = await service.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return job


@router.post(
    "/{job_id}/execute",
    response_model=JobExecutionResponse,
)
async def execute_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
) -> JobExecutionResponse:
    try:
        await service.validate_executable(job_id)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    result = dispatcher.dispatch_job_run(
        job_id=job_id,
    )

    return JobExecutionResponse(
        job_id=job_id,
        execution_id=result.execution_id,
        execution_backend=result.execution_backend,
        execution_kind=result.execution_kind,
        status=result.status,
        queued=result.status == ExecutionStatus.QUEUED,
    )
