from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.episodes.schemas import EpisodeRecord
from sceneops_db.postgres import PostgresEpisodeRepository


class EpisodeStore:
    """EpisodeRecord access. ``insert`` / ``replace_revision`` / ``delete``
    belong to the Episode registrar (``sceneops_worker.episodes.registration``)
    and no other job writes Episode membership."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresEpisodeRepository(session)

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        return await self._repo.get(episode_id)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]:
        return await self._repo.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )

    async def list_recording_scope(
        self, *, dataset_id: str, dataset_version: str, robot_run_id: str
    ) -> list[EpisodeRecord]:
        return await self._repo.list_recording_scope(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
        )

    async def count(self, *, dataset_id: str, dataset_version: str) -> int:
        return await self._repo.count(
            dataset_id=dataset_id, dataset_version=dataset_version
        )

    async def insert(self, episode: EpisodeRecord) -> EpisodeRecord:
        return await self._repo.insert(episode)

    async def replace_revision(self, episode: EpisodeRecord) -> EpisodeRecord:
        return await self._repo.replace_revision(episode)

    async def delete(self, episode_ids: list[str]) -> int:
        return await self._repo.delete(episode_ids)
