from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeRevisionRecord
from sceneops_db.postgres import PostgresEpisodeRepository
from sceneops_db.repositories import RevisionRegistration


class EpisodeStore:
    """Episode unit and revision access. ``register_revision`` belongs to
    the Episode registrar (``sceneops_worker.episodes.registration``); no
    other job writes Episode units or revisions."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresEpisodeRepository(session)

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        return await self._repo.get(episode_id)

    async def list(
        self, *, robot_run_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[EpisodeRecord]:
        return await self._repo.list(
            robot_run_id=robot_run_id, limit=limit, offset=offset
        )

    async def get_revision(
        self, *, episode_id: str, manifest_checksum: str
    ) -> EpisodeRevisionRecord | None:
        return await self._repo.get_revision(
            episode_id=episode_id, manifest_checksum=manifest_checksum
        )

    async def list_revisions(self, episode_id: str) -> list[EpisodeRevisionRecord]:
        return await self._repo.list_revisions(episode_id)

    async def get_revisions(
        self, keys: Sequence[tuple[str, str]]
    ) -> list[EpisodeRevisionRecord]:
        return await self._repo.get_revisions(keys)

    async def list_source_revisions(
        self, *, robot_run_id: str, producer_fingerprint: str
    ) -> list[EpisodeRevisionRecord]:
        return await self._repo.list_source_revisions(
            robot_run_id=robot_run_id, producer_fingerprint=producer_fingerprint
        )

    async def register_revision(
        self, revision: EpisodeRevisionRecord
    ) -> RevisionRegistration[EpisodeRevisionRecord]:
        return await self._repo.register_revision(revision)

    async def get_member_revision(
        self, *, dataset_id: str, dataset_version: str, episode_id: str
    ) -> EpisodeRevisionRecord | None:
        return await self._repo.get_member_revision(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=episode_id,
        )

    async def list_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRevisionRecord]:
        return await self._repo.list_member_revisions(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
            limit=limit,
            offset=offset,
        )
