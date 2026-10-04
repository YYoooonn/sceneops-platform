from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.episodes.schemas import EpisodeRecord
from sceneops_core.runs.schemas import RunStatus, RunType

from sceneops_db.converters.episodes import (
    EpisodeRunRecord,
    episode_model_to_record,
    episode_record_to_values,
    episode_run_model_to_record,
    episode_run_record_to_values,
)
from sceneops_db.models.episodes import EpisodeModel, EpisodeRunRecordModel

from ._utils import apply_pagination, apply_values, enum_value


class PostgresEpisodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        model = await self._get_model(episode_id)
        return episode_model_to_record(model) if model is not None else None

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]:
        stmt = select(EpisodeModel)
        if dataset_id is not None:
            stmt = stmt.where(EpisodeModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(EpisodeModel.dataset_version == dataset_version)
        if robot_run_id is not None:
            stmt = stmt.where(EpisodeModel.robot_run_id == robot_run_id)
        stmt = apply_pagination(
            stmt.order_by(EpisodeModel.episode_id), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [episode_model_to_record(m) for m in result.scalars().all()]

    async def list_recording_scope(
        self, *, dataset_id: str, dataset_version: str, robot_run_id: str
    ) -> list[EpisodeRecord]:
        stmt = (
            select(EpisodeModel)
            .where(EpisodeModel.dataset_id == dataset_id)
            .where(EpisodeModel.dataset_version == dataset_version)
            .where(EpisodeModel.robot_run_id == robot_run_id)
            .order_by(EpisodeModel.episode_id)
        )
        result = await self._session.execute(stmt)
        return [episode_model_to_record(m) for m in result.scalars().all()]

    async def count(
        self, *, dataset_id: str | None = None, dataset_version: str | None = None
    ) -> int:
        """Exact canonical membership count (a SQL COUNT, never a paginated
        list length): the source of DatasetVersion.episode.episode_count."""
        stmt = select(func.count()).select_from(EpisodeModel)
        if dataset_id is not None:
            stmt = stmt.where(EpisodeModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(EpisodeModel.dataset_version == dataset_version)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def insert(self, episode: EpisodeRecord) -> EpisodeRecord:
        model = EpisodeModel(**episode_record_to_values(episode))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return episode_model_to_record(model)

    async def replace_revision(self, episode: EpisodeRecord) -> EpisodeRecord:
        """Repoint an existing Episode at a new manifest revision and refresh
        every projection. Identity and membership must not change."""
        model = await self._get_model(episode.episode_id)
        if model is None:
            raise ValueError(f"Episode not found: {episode.episode_id}")
        if (model.dataset_id, model.dataset_version) != (
            episode.dataset_id,
            episode.dataset_version,
        ):
            raise ValueError(
                f"Episode {episode.episode_id} belongs to "
                f"{model.dataset_id}/{model.dataset_version}, not "
                f"{episode.dataset_id}/{episode.dataset_version}"
            )
        apply_values(model, episode_record_to_values(episode))
        await self._session.flush()
        await self._session.refresh(model)
        return episode_model_to_record(model)

    async def delete(self, episode_ids: list[str]) -> int:
        if not episode_ids:
            return 0
        result = await self._session.execute(
            delete(EpisodeModel).where(EpisodeModel.episode_id.in_(episode_ids))
        )
        await self._session.flush()
        return result.rowcount or 0

    async def _get_model(self, episode_id: str) -> EpisodeModel | None:
        result = await self._session.execute(
            select(EpisodeModel).where(EpisodeModel.episode_id == episode_id)
        )
        return result.scalar_one_or_none()


class PostgresEpisodeRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: EpisodeRunRecord) -> EpisodeRunRecord:
        model = EpisodeRunRecordModel(**episode_run_record_to_values(run))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return episode_run_model_to_record(model)

    async def get(self, run_id: str) -> EpisodeRunRecord | None:
        stmt = select(EpisodeRunRecordModel).where(
            EpisodeRunRecordModel.run_id == run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return episode_run_model_to_record(model) if model is not None else None

    async def update(self, run: EpisodeRunRecord) -> EpisodeRunRecord:
        stmt = select(EpisodeRunRecordModel).where(
            EpisodeRunRecordModel.run_id == run.run_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"EpisodeRun not found: {run.run_id}")
        apply_values(model, episode_run_record_to_values(run))
        await self._session.flush()
        await self._session.refresh(model)
        return episode_run_model_to_record(model)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        episode_id: str | None = None,
        manifest_artifact_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRunRecord]:
        stmt = select(EpisodeRunRecordModel)
        if type is not None:
            stmt = stmt.where(EpisodeRunRecordModel.type == enum_value(type))
        if status is not None:
            stmt = stmt.where(EpisodeRunRecordModel.status == enum_value(status))
        if episode_id is not None:
            stmt = stmt.where(EpisodeRunRecordModel.episode_id == episode_id)
        if manifest_artifact_id is not None:
            stmt = stmt.where(
                EpisodeRunRecordModel.manifest_artifact_id == manifest_artifact_id
            )
        if dataset_id is not None:
            stmt = stmt.where(EpisodeRunRecordModel.dataset_id == dataset_id)
        if dataset_version is not None:
            stmt = stmt.where(EpisodeRunRecordModel.dataset_version == dataset_version)
        if job_id is not None:
            stmt = stmt.where(EpisodeRunRecordModel.job_id == job_id)
        if pipeline_run_id is not None:
            stmt = stmt.where(EpisodeRunRecordModel.pipeline_run_id == pipeline_run_id)
        stmt = apply_pagination(
            stmt.order_by(EpisodeRunRecordModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [episode_run_model_to_record(m) for m in result.scalars().all()]
