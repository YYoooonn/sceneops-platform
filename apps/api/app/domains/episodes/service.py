from __future__ import annotations

from sceneops_core.episodes.schemas import EpisodeProfileRunRecord
from sceneops_core.episodes.schemas.runs import EpisodeValidationRunRecord
from sceneops_core.runs.schemas import RunType
from sceneops_db.repositories.episodes import EpisodeRepository, EpisodeRunRepository

from app.domains.episodes.quality import build_episode_quality
from app.domains.episodes.schemas import (
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeQualityResponse,
)


class EpisodeService:
    """Episode resource service (SceneOps V2 Requests 16-17).

    Wraps EpisodeRepository/EpisodeRunRepository directly — same layering as
    SceneService — and exposes only what EpisodeRecord/EpisodeValidationRunRecord/
    EpisodeProfileRunRecord already persist. No RobotRun/manifest embedding,
    no Scene-domain lookups: an episode-only DatasetVersion (no Scene
    activity at all) works exactly the same as a mixed one, since nothing
    here reads DatasetVersion.scene.
    """

    def __init__(
        self,
        *,
        repository: EpisodeRepository,
        run_repository: EpisodeRunRepository | None = None,
    ) -> None:
        self._repository = repository
        self._run_repository = run_repository

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

    async def get_episode_quality(
        self, episode_id: str
    ) -> EpisodeQualityResponse | None:
        episode = await self._repository.get(episode_id)
        if episode is None:
            return None

        validation_run = await self._latest_episode_validation_run(episode_id)
        profile_run = await self._latest_episode_profile_run(episode_id)

        return build_episode_quality(
            episode=episode,
            validation_run=validation_run,
            profile_run=profile_run,
        )

    async def _latest_episode_validation_run(
        self, episode_id: str
    ) -> EpisodeValidationRunRecord | None:
        if self._run_repository is None:
            return None
        runs = await self._run_repository.list(
            type=RunType.EPISODE_VALIDATION,
            episode_id=episode_id,
            limit=1,
        )
        if not runs:
            return None
        run = runs[0]
        return run if isinstance(run, EpisodeValidationRunRecord) else None

    async def _latest_episode_profile_run(
        self, episode_id: str
    ) -> EpisodeProfileRunRecord | None:
        if self._run_repository is None:
            return None
        runs = await self._run_repository.list(
            type=RunType.EPISODE_PROFILE,
            episode_id=episode_id,
            limit=1,
        )
        if not runs:
            return None
        run = runs[0]
        return run if isinstance(run, EpisodeProfileRunRecord) else None
