from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.scenes.dependencies import SceneServiceDep
from app.platform.artifacts.schemas import ArtifactListResponse
from sceneops_core.scenes.schemas import (
    SceneDetailResponse,
    SceneGenerationMethod,
    SceneListResponse,
    SceneOriginType,
    SceneStatus,
)

router = APIRouter()


@router.get("", response_model=SceneListResponse)
async def list_scenes(
    *,
    service: SceneServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    status: SceneStatus | None = None,
    origin_type: SceneOriginType | None = None,
    generation_method: SceneGenerationMethod | None = None,
) -> SceneListResponse:
    return await service.list_scenes(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        status=status,
        origin_type=origin_type,
        generation_method=generation_method,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{scene_id}", response_model=SceneDetailResponse)
async def get_scene(scene_id: str, service: SceneServiceDep) -> SceneDetailResponse:
    result = await service.get_scene(scene_id)
    if result is None:
        raise_not_found("Scene", scene_id)
    return result


@router.get("/{scene_id}/artifacts", response_model=ArtifactListResponse)
async def list_scene_artifacts(
    scene_id: str,
    service: SceneServiceDep,
    pagination: PaginationDep,
) -> ArtifactListResponse:
    result = await service.list_scene_artifacts(
        scene_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Scene", scene_id)
    return ArtifactListResponse(artifacts=result, count=len(result))
