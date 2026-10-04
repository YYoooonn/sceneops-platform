from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.episodes.dependencies import EpisodeServiceDep
from app.domains.episodes.schemas import (
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeManifestResponse,
    EpisodeQualityResponse,
)

# Read-only: Episode membership is written by the recording_episode_building
# pipeline's registrar, validation / profiling run through pipelines.

router = APIRouter()


@router.get("", response_model=EpisodeListResponse)
async def list_episodes(
    *,
    service: EpisodeServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    robot_run_id: str | None = None,
) -> EpisodeListResponse:
    return await service.list_episodes(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        robot_run_id=robot_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{episode_id}", response_model=EpisodeDetailResponse)
async def get_episode(
    episode_id: str, service: EpisodeServiceDep
) -> EpisodeDetailResponse:
    result = await service.get_episode(episode_id)
    if result is None:
        raise_not_found("Episode", episode_id)
    return result


@router.get("/{episode_id}/manifest", response_model=EpisodeManifestResponse)
async def get_episode_manifest(
    episode_id: str, service: EpisodeServiceDep
) -> EpisodeManifestResponse:
    """The Episode's current canonical manifest revision: asynchronous
    observation / state / action / event streams, each in its own clock."""
    result = await service.get_episode_manifest(episode_id)
    if result is None:
        raise_not_found("Episode", episode_id)
    return result


@router.get("/{episode_id}/quality", response_model=EpisodeQualityResponse)
async def get_episode_quality(
    episode_id: str, service: EpisodeServiceDep
) -> EpisodeQualityResponse:
    result = await service.get_episode_quality(episode_id)
    if result is None:
        raise_not_found("Episode", episode_id)
    return result
