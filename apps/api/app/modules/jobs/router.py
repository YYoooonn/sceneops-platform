from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_job_service
from app.modules.jobs.service import JobService
from sceneops_core.schemas.jobs import (
    CreateJobRequest,
    JobListResponse,
    JobManifest,
    JobStatus,
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


@router.get("/{job_id}", response_model=JobManifest)
async def get_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobManifest:
    job = await service.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return job
