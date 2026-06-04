from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.models.dependencies import ModelServiceDep
from app.platform.artifacts.schemas import ArtifactListResponse
from sceneops_core.models.schemas import (
    CreateModelRequest,
    CreateModelVersionRequest,
    ModelBackend,
    ModelDetailResponse,
    ModelListResponse,
    ModelTaskType,
    ModelVersionDetailResponse,
    ModelVersionListResponse,
    ModelVersionStatus,
)

router = APIRouter()


@router.get("", response_model=ModelListResponse)
async def list_models(
    *,
    service: ModelServiceDep,
    pagination: PaginationDep,
    task_type: ModelTaskType | None = None,
) -> ModelListResponse:
    return await service.list_models(
        task_type=task_type, limit=pagination.limit, offset=pagination.offset
    )


@router.post("", response_model=ModelDetailResponse, status_code=201)
async def create_model(
    request: CreateModelRequest, service: ModelServiceDep
) -> ModelDetailResponse:
    return await service.create_model(request)


@router.get("/{model_id}", response_model=ModelDetailResponse)
async def get_model(model_id: str, service: ModelServiceDep) -> ModelDetailResponse:
    result = await service.get_model(model_id)
    if result is None:
        raise_not_found("Model", model_id)
    return result


@router.get("/{model_id}/versions", response_model=ModelVersionListResponse)
async def list_model_versions(
    model_id: str,
    service: ModelServiceDep,
    pagination: PaginationDep,
    task_type: ModelTaskType | None = None,
    backend: ModelBackend | None = None,
    status: ModelVersionStatus | None = None,
) -> ModelVersionListResponse:
    return await service.list_model_versions(
        model_id,
        task_type=task_type,
        backend=backend,
        status=status,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{model_id}/versions", response_model=ModelVersionDetailResponse, status_code=201
)
async def create_model_version(
    model_id: str,
    request: CreateModelVersionRequest,
    service: ModelServiceDep,
) -> ModelVersionDetailResponse:
    result = await service.create_model_version(model_id, request)
    if result is None:
        raise_not_found("Model", model_id)
    return result


@router.get("/{model_id}/versions/{version}", response_model=ModelVersionDetailResponse)
async def get_model_version(
    model_id: str, version: str, service: ModelServiceDep
) -> ModelVersionDetailResponse:
    result = await service.get_model_version(model_id, version)
    if result is None:
        raise_not_found("Model version", f"{model_id}:{version}")
    return result


@router.get(
    "/{model_id}/versions/{version}/artifacts", response_model=ArtifactListResponse
)
async def list_model_version_artifacts(
    model_id: str,
    version: str,
    service: ModelServiceDep,
    pagination: PaginationDep,
) -> ArtifactListResponse:
    result = await service.list_model_version_artifacts(
        model_id, version, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Model version", f"{model_id}:{version}")
    return ArtifactListResponse(artifacts=result, count=len(result))
