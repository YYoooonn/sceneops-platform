from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.modules.datasets.dependencies import DatasetServiceDep
from sceneops_core.datasets.schemas import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetVersionDetailResponse,
    DatasetVersionListResponse,
    UpsertDatasetRequest,
    UpsertDatasetVersionRequest,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get(
    "",
    response_model=DatasetListResponse,
    response_model_by_alias=True,
)
async def list_datasets(
    service: DatasetServiceDep,
) -> DatasetListResponse:
    return await service.list_datasets()


@router.post(
    "",
    response_model=DatasetDetailResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    request: CreateDatasetRequest,
    service: DatasetServiceDep,
) -> DatasetDetailResponse:
    return await service.create_dataset(request)


@router.get(
    "/{dataset_id}",
    response_model=DatasetDetailResponse,
    response_model_by_alias=True,
)
async def get_dataset(
    dataset_id: str,
    service: DatasetServiceDep,
) -> DatasetDetailResponse:
    response = await service.get_dataset(dataset_id)

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset not found: {dataset_id}",
        )

    return response


@router.put(
    "/{dataset_id}",
    response_model=DatasetDetailResponse,
    response_model_by_alias=True,
)
async def upsert_dataset(
    dataset_id: str,
    request: UpsertDatasetRequest,
    service: DatasetServiceDep,
) -> DatasetDetailResponse:
    return await service.upsert_dataset(dataset_id, request)


@router.get(
    "/{dataset_id}/versions",
    response_model=DatasetVersionListResponse,
    response_model_by_alias=True,
)
async def list_dataset_versions(
    dataset_id: str,
    service: DatasetServiceDep,
) -> DatasetVersionListResponse:
    response = await service.list_dataset_versions(dataset_id)

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset not found: {dataset_id}",
        )

    return response


@router.post(
    "/{dataset_id}/versions",
    response_model=DatasetVersionDetailResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset_version(
    dataset_id: str,
    request: CreateDatasetVersionRequest,
    service: DatasetServiceDep,
) -> DatasetVersionDetailResponse:
    response = await service.create_dataset_version(dataset_id, request)

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset not found: {dataset_id}",
        )

    return response


@router.get(
    "/{dataset_id}/versions/{version}",
    response_model=DatasetVersionDetailResponse,
    response_model_by_alias=True,
)
async def get_dataset_version(
    dataset_id: str,
    version: str,
    service: DatasetServiceDep,
) -> DatasetVersionDetailResponse:
    response = await service.get_dataset_version(
        dataset_id=dataset_id,
        version=version,
    )

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset version not found: {dataset_id}:{version}",
        )

    return response


@router.put(
    "/{dataset_id}/versions/{version}",
    response_model=DatasetVersionDetailResponse,
    response_model_by_alias=True,
)
async def upsert_dataset_version(
    dataset_id: str,
    version: str,
    request: UpsertDatasetVersionRequest,
    service: DatasetServiceDep,
) -> DatasetVersionDetailResponse:
    response = await service.upsert_dataset_version(
        dataset_id=dataset_id,
        version=version,
        request=request,
    )

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset not found: {dataset_id}",
        )

    return response
