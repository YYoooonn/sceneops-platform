from fastapi import APIRouter, Depends, Query, status

from sceneops_core.schemas.jobs import (
    CreateJobRequest,
    JobListResponse,
    JobManifest,
    JobStatus,
    JobType,
)

from app.core.dependencies import get_job_service
from app.modules.jobs.service import JobService
from app.shared.errors import not_found

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobManifest,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    request: CreateJobRequest,
    service: JobService = Depends(get_job_service),
):
    return service.create_job(request)


@router.get(
    "",
    response_model=JobListResponse,
)
def list_jobs(
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    job_type: JobType | None = Query(default=None, alias="type"),
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    dataset_version: str | None = Query(default=None, alias="datasetVersion"),
    service: JobService = Depends(get_job_service),
):
    return service.list_jobs(
        status=status_filter,
        job_type=job_type.value if job_type is not None else None,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


@router.get(
    "/{job_id}",
    response_model=JobManifest,
)
def get_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
):
    job = service.get_job(job_id)

    if job is None:
        raise not_found("Job not found")

    return job
