from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord
from sceneops_db.postgres import (
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)


class DatasetStore:
    def __init__(self, session: AsyncSession) -> None:
        self._datasets = PostgresDatasetRepository(session)
        self._versions = PostgresDatasetVersionRepository(session)

    async def get_dataset(self, dataset_id: str) -> DatasetRecord | None:
        return await self._datasets.get(dataset_id)

    async def create_dataset(self, dataset: DatasetRecord) -> DatasetRecord:
        return await self._datasets.create(dataset)

    async def save_dataset(self, dataset: DatasetRecord) -> DatasetRecord:
        return await self._datasets.update(dataset)

    async def get_version(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None:
        return await self._versions.get(dataset_id=dataset_id, version=version)

    async def create_version(
        self, version: DatasetVersionRecord
    ) -> DatasetVersionRecord:
        return await self._versions.create(version)

    async def save_version(self, version: DatasetVersionRecord) -> DatasetVersionRecord:
        return await self._versions.update(version)

    async def upsert_version(
        self, version: DatasetVersionRecord
    ) -> DatasetVersionRecord:
        return await self._versions.upsert(version)

    async def lock_version_for_update(
        self, *, dataset_id: str, version: str
    ) -> DatasetVersionRecord:
        return await self._versions.lock_for_update(
            dataset_id=dataset_id, version=version
        )

    async def replace_scene_membership_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        scene_count: int,
        keyframe_count: int,
        observation_count: int,
        observed_channels: list[str],
    ) -> DatasetVersionRecord:
        """Scene registrar only, under ``lock_version_for_update``."""
        return await self._versions.replace_scene_membership_summary(
            dataset_id=dataset_id,
            version=version,
            scene_count=scene_count,
            keyframe_count=keyframe_count,
            observation_count=observation_count,
            observed_channels=observed_channels,
        )

    async def update_episode_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        episode_count: int | None = None,
    ) -> DatasetVersionRecord:
        return await self._versions.update_episode_summary(
            dataset_id=dataset_id,
            version=version,
            episode_count=episode_count,
        )
