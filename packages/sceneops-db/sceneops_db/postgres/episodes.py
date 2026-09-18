from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeStatus

from sceneops_db.converters.episodes import (
    episode_model_to_record,
    episode_record_to_values,
)
from sceneops_db.models.episodes import EpisodeModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresEpisodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, episode: EpisodeRecord) -> EpisodeRecord:
        model = EpisodeModel(**episode_record_to_values(episode))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return episode_model_to_record(model)

    async def upsert(self, episode: EpisodeRecord) -> EpisodeRecord:
        existing = await self.get(episode.episode_id)
        if existing is None:
            return await self.create(episode)
        return await self.update(episode)

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        stmt = select(EpisodeModel).where(EpisodeModel.episode_id == episode_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return episode_model_to_record(model) if model is not None else None

    async def update(self, episode: EpisodeRecord) -> EpisodeRecord:
        stmt = select(EpisodeModel).where(EpisodeModel.episode_id == episode.episode_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Episode not found: {episode.episode_id}")
        apply_values(model, episode_record_to_values(episode))
        await self._session.flush()
        await self._session.refresh(model)
        return episode_model_to_record(model)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: EpisodeStatus | None = None,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        mission_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]:
        stmt = select(EpisodeModel)
        if dataset_id is not None:
            stmt = stmt.where(EpisodeModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(EpisodeModel.dataset_version == dataset_version)
        if status is not None:
            stmt = stmt.where(EpisodeModel.status == enum_value(status))
        if robot_id is not None:
            stmt = stmt.where(EpisodeModel.robot_id == robot_id)
        if robot_run_id is not None:
            stmt = stmt.where(EpisodeModel.robot_run_id == robot_run_id)
        if mission_id is not None:
            stmt = stmt.where(EpisodeModel.mission_id == mission_id)
        stmt = apply_pagination(
            stmt.order_by(EpisodeModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [episode_model_to_record(m) for m in result.scalars().all()]
