from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.inference.dependencies import InferenceServiceDep
from app.domains.inference.schemas import (
    InferenceMetricsResponse,
    InferenceRunListResponse,
    InferenceRunResponse,
)
from app.platform.artifacts.schemas import ArtifactListResponse
from sceneops_core.runs.schemas import RunStatus

router = APIRouter()


@router.get("/runs", response_model=InferenceRunListResponse)
async def list_inference_runs(
    *,
    service: InferenceServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    status: RunStatus | None = None,
    job_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> InferenceRunListResponse:
    return await service.list_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        status=status,
        job_id=job_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/runs/{inference_run_id}", response_model=InferenceRunResponse)
async def get_inference_run(
    inference_run_id: str,
    service: InferenceServiceDep,
) -> InferenceRunResponse:
    result = await service.get_run(inference_run_id)
    if result is None:
        raise_not_found("Inference run", inference_run_id)
    return result


@router.get("/runs/{inference_run_id}/metrics", response_model=InferenceMetricsResponse)
async def get_inference_run_metrics(
    inference_run_id: str,
    service: InferenceServiceDep,
) -> InferenceMetricsResponse:
    result = await service.get_run_metrics(inference_run_id)
    if result is None:
        raise_not_found("Inference run", inference_run_id)
    return result


@router.get("/runs/{inference_run_id}/artifacts", response_model=ArtifactListResponse)
async def list_inference_run_artifacts(
    inference_run_id: str,
    service: InferenceServiceDep,
    pagination: PaginationDep,
) -> ArtifactListResponse:
    result = await service.list_run_artifacts(
        inference_run_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Inference run", inference_run_id)
    return ArtifactListResponse(artifacts=result, count=len(result))
