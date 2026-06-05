from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.datasets.dependencies import DatasetServiceDep
from app.domains.datasets.schemas import (
    CreateDatasetVersionBody,
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetVersionDetailResponse,
    DatasetVersionListResponse,
    DatasetVersionQualityResponse,
    UpdateDatasetRequest,
    UpdateDatasetVersionRequest,
)
from app.domains.scenes.schemas import SceneListResponse
from app.platform.artifacts.schemas import ArtifactListResponse
from sceneops_core.datasets.schemas import CreateDatasetRequest
from sceneops_core.datasets.schemas.enums import DatasetStatus, DatasetType

router = APIRouter()


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    *,
    service: DatasetServiceDep,
    pagination: PaginationDep,
    type: DatasetType | None = None,
    status: DatasetStatus | None = None,
) -> DatasetListResponse:
    return await service.list_datasets(
        type=type, status=status, limit=pagination.limit, offset=pagination.offset
    )


@router.post("", response_model=DatasetDetailResponse, status_code=201)
async def create_dataset(
    request: CreateDatasetRequest,
    service: DatasetServiceDep,
) -> DatasetDetailResponse:
    return await service.create_dataset(request)


@router.get("/{dataset_id}", response_model=DatasetDetailResponse)
async def get_dataset(
    dataset_id: str, service: DatasetServiceDep
) -> DatasetDetailResponse:
    result = await service.get_dataset(dataset_id)
    if result is None:
        raise_not_found("Dataset", dataset_id)
    return result


@router.patch("/{dataset_id}", response_model=DatasetDetailResponse)
async def update_dataset(
    dataset_id: str,
    request: UpdateDatasetRequest,
    service: DatasetServiceDep,
) -> DatasetDetailResponse:
    result = await service.update_dataset(dataset_id, request)
    if result is None:
        raise_not_found("Dataset", dataset_id)
    return result


@router.get("/{dataset_id}/versions", response_model=DatasetVersionListResponse)
async def list_dataset_versions(
    dataset_id: str,
    service: DatasetServiceDep,
    pagination: PaginationDep,
) -> DatasetVersionListResponse:
    result = await service.list_dataset_versions(
        dataset_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Dataset", dataset_id)
    return result


@router.post(
    "/{dataset_id}/versions",
    response_model=DatasetVersionDetailResponse,
    status_code=201,
)
async def create_dataset_version(
    dataset_id: str,
    body: CreateDatasetVersionBody,
    service: DatasetServiceDep,
) -> DatasetVersionDetailResponse:
    result = await service.create_dataset_version(dataset_id, body)
    if result is None:
        raise_not_found("Dataset", dataset_id)
    return result


@router.get(
    "/{dataset_id}/versions/{version}", response_model=DatasetVersionDetailResponse
)
async def get_dataset_version(
    dataset_id: str, version: str, service: DatasetServiceDep
) -> DatasetVersionDetailResponse:
    result = await service.get_dataset_version(dataset_id, version)
    if result is None:
        raise_not_found("Dataset version", f"{dataset_id}:{version}")
    return result


@router.patch(
    "/{dataset_id}/versions/{version}", response_model=DatasetVersionDetailResponse
)
async def update_dataset_version(
    dataset_id: str,
    version: str,
    request: UpdateDatasetVersionRequest,
    service: DatasetServiceDep,
) -> DatasetVersionDetailResponse:
    result = await service.update_dataset_version(dataset_id, version, request)
    if result is None:
        raise_not_found("Dataset version", f"{dataset_id}:{version}")
    return result


@router.get(
    "/{dataset_id}/versions/{version}/quality",
    response_model=DatasetVersionQualityResponse,
)
async def get_dataset_version_quality(
    dataset_id: str, version: str, service: DatasetServiceDep
) -> DatasetVersionQualityResponse:
    result = await service.get_dataset_version_quality(dataset_id, version)
    if result is None:
        raise_not_found("Dataset version", f"{dataset_id}:{version}")
    return result


@router.get("/{dataset_id}/versions/{version}/scenes", response_model=SceneListResponse)
async def list_dataset_version_scenes(
    dataset_id: str,
    version: str,
    service: DatasetServiceDep,
    pagination: PaginationDep,
) -> SceneListResponse:
    result = await service.list_dataset_version_scenes(
        dataset_id, version, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Dataset version", f"{dataset_id}:{version}")
    return result


@router.get(
    "/{dataset_id}/versions/{version}/artifacts", response_model=ArtifactListResponse
)
async def list_dataset_version_artifacts(
    dataset_id: str,
    version: str,
    service: DatasetServiceDep,
    pagination: PaginationDep,
) -> ArtifactListResponse:
    result = await service.list_dataset_version_artifacts(
        dataset_id, version, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Dataset version", f"{dataset_id}:{version}")
    return ArtifactListResponse(artifacts=result, count=len(result))
