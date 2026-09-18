from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.episodes.dependencies import EpisodeServiceDep
from app.domains.episodes.schemas import EpisodeDetailResponse, EpisodeListResponse

# No /{episode_id}/artifacts or /{episode_id}/manifest endpoint here — see
# SceneOps V2 Request 16. Unlike ArtifactModel.scene_id (a dedicated indexed
# column backing Scene's convenience endpoint), episodes are only reachable
# through the generic owner_type/owner_id columns, so
# GET /artifacts?owner_type=episode&owner_id={episode_id} already answers
# "what artifacts does this episode own" with no new code, and
# EpisodeRecord.episode_manifest_uri already answers "where is the
# manifest" directly on the detail response.

router = APIRouter()


@router.get("", response_model=EpisodeListResponse)
async def list_episodes(
    *,
    service: EpisodeServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    robot_run_id: str | None = None,
    mission_id: str | None = None,
) -> EpisodeListResponse:
    return await service.list_episodes(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        robot_run_id=robot_run_id,
        mission_id=mission_id,
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
