from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.core.revisions import RevisionAddressDep
from app.domains.scenes.dependencies import SceneServiceDep
from app.domains.scenes.schemas import (
    SceneDetailResponse,
    SceneListResponse,
    SceneQualityResponse,
)
from app.platform.artifacts.schemas import ArtifactListResponse

router = APIRouter()


@router.get("", response_model=SceneListResponse)
async def list_scenes(
    *,
    service: SceneServiceDep,
    pagination: PaginationDep,
    robot_run_id: str | None = None,
) -> SceneListResponse:
    """Scene units; the Scenes of a DatasetVersion, each at the revision it
    pins, are listed under the DatasetVersion."""
    return await service.list_scenes(
        robot_run_id=robot_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{scene_id}", response_model=SceneDetailResponse)
async def get_scene(scene_id: str, service: SceneServiceDep) -> SceneDetailResponse:
    result = await service.get_scene(scene_id)
    if result is None:
        raise_not_found("Scene", scene_id)
    return result


@router.get("/{scene_id}/quality", response_model=SceneQualityResponse)
async def get_scene_quality(
    scene_id: str, address: RevisionAddressDep, service: SceneServiceDep
) -> SceneQualityResponse:
    """Quality of one revision of the Scene: the one ``manifest_checksum``
    names, or the one the DatasetVersion pins."""
    result = await service.get_scene_quality(scene_id, address)
    if result is None:
        raise_not_found("Scene revision", address.describe(scene_id))
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
