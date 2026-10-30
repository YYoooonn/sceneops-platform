from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.datasets.schemas import (
    DatasetRecord,
    DatasetVersionMember,
    DatasetVersionRecord,
)
from sceneops_core.provenance import CanonicalDomain
from sceneops_db.postgres import (
    PostgresDatasetRepository,
    PostgresDatasetVersionMemberRepository,
    PostgresDatasetVersionRepository,
)


class DatasetStore:
    """Datasets and committed DatasetVersions. ``insert_version`` belongs to
    COMPOSE_DATASET_VERSION (``sceneops_worker.datasets.composition``); no
    other job writes a DatasetVersion or its members."""

    def __init__(self, session: AsyncSession) -> None:
        self._datasets = PostgresDatasetRepository(session)
        self._versions = PostgresDatasetVersionRepository(session)
        self._members = PostgresDatasetVersionMemberRepository(session)

    async def get_dataset(self, dataset_id: str) -> DatasetRecord | None:
        return await self._datasets.get(dataset_id)

    async def create_dataset(self, dataset: DatasetRecord) -> DatasetRecord:
        return await self._datasets.create(dataset)

    async def get_version(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None:
        return await self._versions.get(dataset_id=dataset_id, version=version)

    async def insert_version(
        self,
        version: DatasetVersionRecord,
        members: Sequence[DatasetVersionMember],
    ) -> DatasetVersionRecord:
        return await self._versions.insert(version, members)

    async def list_members(
        self,
        *,
        dataset_id: str,
        version: str,
        domain: CanonicalDomain | None = None,
    ) -> list[DatasetVersionMember]:
        return await self._members.list(
            dataset_id=dataset_id, version=version, domain=domain
        )
