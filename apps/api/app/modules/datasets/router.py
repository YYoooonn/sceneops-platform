from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.modules.datasets.dependencies import DatasetServiceDep, SceneBuildingServiceDep
from sceneops_core.datasets.schemas import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetVersionDetailResponse,
    DatasetVersionListResponse,
    RawLogManifest,
    SceneSegmentListResponse,
    SceneSegmentManifest,
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


# ── Scene building endpoints ──────────────────────────────────────────────────


@router.get(
    "/{dataset_id}/versions/{version}/raw-log",
    response_model=RawLogManifest,
    response_model_by_alias=True,
    tags=["scene-building"],
)
async def get_raw_log(
    dataset_id: str,
    version: str,
    service: SceneBuildingServiceDep,
) -> RawLogManifest:
    return await service.get_raw_log(
        dataset_id=dataset_id,
        dataset_version=version,
    )


@router.get(
    "/{dataset_id}/versions/{version}/scene-segments",
    response_model=SceneSegmentListResponse,
    response_model_by_alias=True,
    tags=["scene-building"],
)
async def list_scene_segments(
    dataset_id: str,
    version: str,
    service: SceneBuildingServiceDep,
    channel: str | None = Query(
        default=None, description="Filter by channel (e.g. CAM_FRONT)"
    ),
    valid_only: bool = Query(
        default=False, description="Only segments within timestamp gap policy"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> SceneSegmentListResponse:
    return await service.list_scene_segments(
        dataset_id=dataset_id,
        dataset_version=version,
        channel=channel,
        valid_only=valid_only,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{dataset_id}/versions/{version}/scene-segments/{segment_id}",
    response_model=SceneSegmentManifest,
    response_model_by_alias=True,
    tags=["scene-building"],
)
async def get_scene_segment(
    dataset_id: str,
    version: str,
    segment_id: str,
    service: SceneBuildingServiceDep,
) -> SceneSegmentManifest:
    return await service.get_scene_segment(
        dataset_id=dataset_id,
        dataset_version=version,
        segment_id=segment_id,
    )
