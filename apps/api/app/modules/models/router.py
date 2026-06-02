from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.modules.models.dependencies import ModelServiceDep
from sceneops_core.models.schemas import (
    CreateModelRequest,
    CreateModelVersionRequest,
    ModelDetailResponse,
    ModelListResponse,
    ModelVersionDetailResponse,
    ModelVersionListResponse,
)

router = APIRouter(
    prefix="/models",
    tags=["models"],
)


@router.get("", response_model=ModelListResponse, response_model_by_alias=True)
async def list_models(
    service: ModelServiceDep,
) -> ModelListResponse:
    return await service.list_models()


@router.post(
    "",
    response_model=ModelDetailResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    request: CreateModelRequest,
    service: ModelServiceDep,
) -> ModelDetailResponse:
    return await service.create_model(request)


@router.get(
    "/{model_id}",
    response_model=ModelDetailResponse,
    response_model_by_alias=True,
)
async def get_model(
    model_id: str,
    service: ModelServiceDep,
) -> ModelDetailResponse:
    response = await service.get_model(model_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    return response


@router.get(
    "/{model_id}/versions",
    response_model=ModelVersionListResponse,
    response_model_by_alias=True,
)
async def list_model_versions(
    model_id: str,
    service: ModelServiceDep,
) -> ModelVersionListResponse:
    return await service.list_model_versions(model_id)


@router.post(
    "/{model_id}/versions",
    response_model=ModelVersionDetailResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_model_version(
    model_id: str,
    request: CreateModelVersionRequest,
    service: ModelServiceDep,
) -> ModelVersionDetailResponse:
    return await service.create_model_version(model_id, request)


@router.get(
    "/{model_id}/versions/{version}",
    response_model=ModelVersionDetailResponse,
    response_model_by_alias=True,
)
async def get_model_version(
    model_id: str,
    version: str,
    service: ModelServiceDep,
) -> ModelVersionDetailResponse:
    response = await service.get_model_version(model_id, version)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model version not found: {model_id}:{version}",
        )
    return response
