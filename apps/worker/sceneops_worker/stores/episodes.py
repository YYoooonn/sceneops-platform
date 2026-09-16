from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeStatus
from sceneops_db.postgres import PostgresEpisodeRepository


class EpisodeStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresEpisodeRepository(session)

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        return await self._repo.get(episode_id)

    async def create(self, episode: EpisodeRecord) -> EpisodeRecord:
        return await self._repo.create(episode)

    async def save(self, episode: EpisodeRecord) -> EpisodeRecord:
        return await self._repo.update(episode)

    async def upsert(self, episode: EpisodeRecord) -> EpisodeRecord:
        return await self._repo.upsert(episode)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: EpisodeStatus | None = None,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]:
        return await self._repo.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            status=status,
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )
