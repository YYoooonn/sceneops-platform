from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeRevisionRecord
from sceneops_core.runs.schemas import RunStatus, RunType

from sceneops_db.converters.datasets import make_dataset_version_id
from sceneops_db.converters.episodes import (
    EpisodeRunRecord,
    episode_model_to_record,
    episode_revision_model_to_record,
    episode_revision_to_values,
    episode_run_model_to_record,
    episode_run_record_to_values,
    episode_unit_to_values,
)
from sceneops_db.models.datasets import DatasetVersionMemberModel
from sceneops_db.models.episodes import (
    EpisodeModel,
    EpisodeRevisionModel,
    EpisodeRunRecordModel,
)
from sceneops_db.repositories.revisions import RevisionRegistration

from ._revisions import register_unit_revision
from ._utils import IN_CLAUSE_CHUNK, apply_pagination, apply_values, enum_value


class PostgresEpisodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- units ------------------------------------------------------------------

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        model = (
            await self._session.execute(
                select(EpisodeModel).where(EpisodeModel.episode_id == episode_id)
            )
        ).scalar_one_or_none()
        return episode_model_to_record(model) if model is not None else None

    async def list(
        self,
        *,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]:
        stmt = select(EpisodeModel)
        if robot_run_id is not None:
            stmt = stmt.where(EpisodeModel.robot_run_id == robot_run_id)
        stmt = apply_pagination(
            stmt.order_by(EpisodeModel.episode_id), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [episode_model_to_record(m) for m in result.scalars().all()]

    # --- revisions --------------------------------------------------------------

    async def get_revision(
        self, *, episode_id: str, manifest_checksum: str
    ) -> EpisodeRevisionRecord | None:
        rows = await self._revisions(
            EpisodeRevisionModel.episode_id == episode_id,
            EpisodeRevisionModel.manifest_checksum == manifest_checksum,
        )
        return rows[0] if rows else None

    async def list_revisions(self, episode_id: str) -> list[EpisodeRevisionRecord]:
        return await self._revisions(EpisodeRevisionModel.episode_id == episode_id)

    async def get_revisions(
        self, keys: Sequence[tuple[str, str]]
    ) -> list[EpisodeRevisionRecord]:
        unique = sorted(set(keys))
        found: list[EpisodeRevisionRecord] = []
        for start in range(0, len(unique), IN_CLAUSE_CHUNK):
            found += await self._revisions(
                tuple_(
                    EpisodeRevisionModel.episode_id,
                    EpisodeRevisionModel.manifest_checksum,
                ).in_(unique[start : start + IN_CLAUSE_CHUNK])
            )
        return found

    async def list_source_revisions(
        self, *, robot_run_id: str, producer_fingerprint: str
    ) -> list[EpisodeRevisionRecord]:
        return await self._revisions(
            EpisodeModel.robot_run_id == robot_run_id,
            EpisodeRevisionModel.producer_fingerprint == producer_fingerprint,
        )

    async def register_revision(
        self, revision: EpisodeRevisionRecord
    ) -> RevisionRegistration[EpisodeRevisionRecord]:
        return await register_unit_revision(
            self._session,
            unit_model=EpisodeModel,
            revision_model=EpisodeRevisionModel,
            id_column="episode_id",
            unit_values=episode_unit_to_values(revision),
            revision_values=episode_revision_to_values(revision),
            candidate=revision,
            list_revisions=self.list_revisions,
        )

    # --- the revisions a DatasetVersion pins -----------------------------------

    async def get_member_revision(
        self, *, dataset_id: str, dataset_version: str, episode_id: str
    ) -> EpisodeRevisionRecord | None:
        rows = await self._revisions(
            DatasetVersionMemberModel.episode_id == episode_id,
            dataset_version_id=make_dataset_version_id(dataset_id, dataset_version),
        )
        return rows[0] if rows else None

    async def list_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRevisionRecord]:
        conditions = (
            [] if robot_run_id is None else [EpisodeModel.robot_run_id == robot_run_id]
        )
        return await self._revisions(
            *conditions,
            dataset_version_id=make_dataset_version_id(dataset_id, dataset_version),
            limit=limit,
            offset=offset,
        )

    async def _revisions(
        self,
        *conditions: Any,
        dataset_version_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[EpisodeRevisionRecord]:
        stmt = select(EpisodeRevisionModel, EpisodeModel).join(
            EpisodeModel, EpisodeModel.episode_id == EpisodeRevisionModel.episode_id
        )
        if dataset_version_id is not None:
            stmt = stmt.join(
                DatasetVersionMemberModel,
                and_(
                    DatasetVersionMemberModel.dataset_version_id == dataset_version_id,
                    DatasetVersionMemberModel.episode_id
                    == EpisodeRevisionModel.episode_id,
                    DatasetVersionMemberModel.manifest_checksum
                    == EpisodeRevisionModel.manifest_checksum,
                ),
            )
        stmt = stmt.where(*conditions).order_by(
            EpisodeRevisionModel.episode_id, EpisodeRevisionModel.manifest_checksum
        )
        if limit is not None:
            stmt = apply_pagination(stmt, limit=limit, offset=offset)
        result = await self._session.execute(stmt)
        return [
            episode_revision_model_to_record(revision, unit)
            for revision, unit in result.all()
        ]


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
