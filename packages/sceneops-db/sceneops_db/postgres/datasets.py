from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.datasets.schemas import (
    DatasetRecord,
    DatasetVersionMember,
    DatasetVersionRecord,
)
from sceneops_core.provenance import CanonicalDomain

from sceneops_db.converters.datasets import (
    dataset_model_to_record,
    dataset_record_to_values,
    dataset_version_model_to_record,
    dataset_version_record_to_values,
    make_dataset_version_id,
)
from sceneops_db.models.datasets import (
    DatasetModel,
    DatasetVersionMemberModel,
    DatasetVersionModel,
)

from sceneops_db.repositories.datasets import DatasetVersionExistsError

from ._utils import IN_CLAUSE_CHUNK, apply_pagination, apply_values


class PostgresDatasetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, dataset: DatasetRecord) -> DatasetRecord:
        model = DatasetModel(**dataset_record_to_values(dataset))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_model_to_record(model)

    async def upsert(self, dataset: DatasetRecord) -> DatasetRecord:
        existing = await self.get(dataset.dataset_id)
        if existing is None:
            return await self.create(dataset)
        return await self.update(dataset)

    async def get(self, dataset_id: str) -> DatasetRecord | None:
        stmt = select(DatasetModel).where(DatasetModel.dataset_id == dataset_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return dataset_model_to_record(model) if model is not None else None

    async def update(self, dataset: DatasetRecord) -> DatasetRecord:
        stmt = select(DatasetModel).where(DatasetModel.dataset_id == dataset.dataset_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Dataset not found: {dataset.dataset_id}")
        apply_values(model, dataset_record_to_values(dataset))
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_model_to_record(model)

    async def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRecord]:
        stmt = select(DatasetModel)
        stmt = apply_pagination(
            stmt.order_by(DatasetModel.created_at.desc()), limit=limit, offset=offset
        )
        result = await self._session.execute(stmt)
        return [dataset_model_to_record(m) for m in result.scalars().all()]


class PostgresDatasetVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None:
        version_id = make_dataset_version_id(dataset_id, version)
        stmt = select(DatasetVersionModel).where(DatasetVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return dataset_version_model_to_record(model) if model is not None else None

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]:
        stmt = select(DatasetVersionModel)
        if dataset_id is not None:
            stmt = stmt.where(DatasetVersionModel.dataset_id == dataset_id)
        stmt = apply_pagination(
            stmt.order_by(
                DatasetVersionModel.created_at.desc(), DatasetVersionModel.id
            ),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [dataset_version_model_to_record(m) for m in result.scalars().all()]

    async def insert(
        self,
        version: DatasetVersionRecord,
        members: Sequence[DatasetVersionMember],
    ) -> DatasetVersionRecord:
        _check_members_match_summaries(version, members)
        version_id = make_dataset_version_id(version.dataset_id, version.version)
        # The version row goes first: a concurrent composition of the same
        # name blocks on its key here, before either writes a member, and
        # fails when this one commits. The savepoint keeps the caller's
        # transaction usable after that failure.
        try:
            async with self._session.begin_nested():
                await self._session.execute(
                    insert(DatasetVersionModel).values(
                        **dataset_version_record_to_values(version)
                    )
                )
        except IntegrityError as exc:
            if _constraint_name(exc) in _VERSION_NAME_CONSTRAINTS:
                raise DatasetVersionExistsError(
                    version.dataset_id, version.version
                ) from exc
            raise
        for start in range(0, len(members), IN_CLAUSE_CHUNK):
            await self._session.execute(
                insert(DatasetVersionMemberModel),
                [
                    _member_values(version_id, m)
                    for m in members[start : start + IN_CLAUSE_CHUNK]
                ],
            )
        committed = await self.get(
            dataset_id=version.dataset_id, version=version.version
        )
        assert committed is not None
        return committed


_VERSION_NAME_CONSTRAINTS = frozenset(
    {"dataset_versions_pkey", "uq_dataset_versions_dataset_id_version"}
)


def _constraint_name(exc: IntegrityError) -> str | None:
    # asyncpg's exception, wrapped by the SQLAlchemy DBAPI adapter.
    return getattr(exc.orig.__cause__, "constraint_name", None)


def _check_members_match_summaries(
    version: DatasetVersionRecord, members: Sequence[DatasetVersionMember]
) -> None:
    if not members:
        raise ValueError("a DatasetVersion has at least one member")
    scenes = sum(1 for m in members if m.domain == "scene")
    expected = (
        version.scene.scene_count if version.scene else 0,
        version.episode.episode_count if version.episode else 0,
    )
    if (scenes, len(members) - scenes) != expected:
        raise ValueError(
            f"DatasetVersion {version.dataset_id}/{version.version}: summaries count "
            f"{expected} (scenes, episodes) but the members are "
            f"{(scenes, len(members) - scenes)}"
        )


def _member_values(version_id: str, member: DatasetVersionMember) -> dict:
    return {
        "dataset_version_id": version_id,
        "unit_id": member.unit_id,
        "domain": member.domain,
        "scene_id": member.unit_id if member.domain == "scene" else None,
        "episode_id": member.unit_id if member.domain == "episode" else None,
        "manifest_artifact_id": member.manifest_artifact_id,
        "manifest_checksum": member.manifest_checksum,
    }


class PostgresDatasetVersionMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        *,
        dataset_id: str,
        version: str,
        domain: CanonicalDomain | None = None,
    ) -> list[DatasetVersionMember]:
        stmt = select(DatasetVersionMemberModel).where(
            DatasetVersionMemberModel.dataset_version_id
            == make_dataset_version_id(dataset_id, version)
        )
        if domain is not None:
            stmt = stmt.where(DatasetVersionMemberModel.domain == domain)
        result = await self._session.execute(
            stmt.order_by(DatasetVersionMemberModel.unit_id)
        )
        return [
            DatasetVersionMember(
                domain=m.domain,
                unit_id=m.unit_id,
                manifest_artifact_id=m.manifest_artifact_id,
                manifest_checksum=m.manifest_checksum,
            )
            for m in result.scalars().all()
        ]
