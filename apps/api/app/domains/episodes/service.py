from __future__ import annotations

from sceneops_db.repositories.episodes import EpisodeRepository

from app.domains.episodes.schemas import EpisodeDetailResponse, EpisodeListResponse


class EpisodeService:
    """Read-only Episode resource service (SceneOps V2 Request 16).

    Wraps EpisodeRepository directly — same layering as SceneService — and
    exposes only what EpisodeRecord already persists. No RobotRun/manifest
    embedding, no Scene-domain lookups: an episode-only DatasetVersion (no
    Scene activity at all) works exactly the same as a mixed one, since
    nothing here reads DatasetVersion.scene.
    """

    def __init__(self, *, repository: EpisodeRepository) -> None:
        self._repository = repository

    async def list_episodes(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        robot_run_id: str | None = None,
        mission_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> EpisodeListResponse:
        episodes = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
            mission_id=mission_id,
            limit=limit,
            offset=offset,
        )
        return EpisodeListResponse(episodes=episodes, count=len(episodes))

    async def get_episode(self, episode_id: str) -> EpisodeDetailResponse | None:
        episode = await self._repository.get(episode_id)
        if episode is None:
            return None
        return EpisodeDetailResponse(episode=episode)
