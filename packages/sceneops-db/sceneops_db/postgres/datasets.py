from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, select
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

from ._utils import apply_pagination, apply_values


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

    async def create(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        model = DatasetVersionModel(**dataset_version_record_to_values(version))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_version_model_to_record(model)

    async def upsert(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        existing = await self.get(
            dataset_id=version.dataset_id, version=version.version
        )
        if existing is None:
            return await self.create(version)
        return await self.update(version)

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

    async def update(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        version_id = make_dataset_version_id(version.dataset_id, version.version)
        stmt = select(DatasetVersionModel).where(DatasetVersionModel.id == version_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(
                f"DatasetVersion not found: {version.dataset_id}/{version.version}"
            )
        apply_values(model, dataset_version_record_to_values(version))
        await self._session.flush()
        await self._session.refresh(model)
        return dataset_version_model_to_record(model)

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
            stmt.order_by(DatasetVersionModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [dataset_version_model_to_record(m) for m in result.scalars().all()]


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

    async def add(
        self,
        *,
        dataset_id: str,
        version: str,
        members: Sequence[DatasetVersionMember],
    ) -> None:
        if not members:
            return
        version_id = make_dataset_version_id(dataset_id, version)
        await self._session.execute(
            insert(DatasetVersionMemberModel),
            [
                {
                    "dataset_version_id": version_id,
                    "unit_id": m.unit_id,
                    "domain": m.domain,
                    "scene_id": m.unit_id if m.domain == "scene" else None,
                    "episode_id": m.unit_id if m.domain == "episode" else None,
                    "manifest_artifact_id": m.manifest_artifact_id,
                    "manifest_checksum": m.manifest_checksum,
                }
                for m in members
            ],
        )
