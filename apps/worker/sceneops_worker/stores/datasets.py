from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.datasets.schemas import (
    DatasetRecord,
    DatasetVersionRecord,
    DatasetValidationStatus,
)
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

    async def update_scene_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        scene_count: int | None = None,
        sample_count: int | None = None,
        frame_count: int | None = None,
        channels: list[str] | None = None,
        required_channels: list[str] | None = None,
        manifest_uri: str | None = None,
        raw_source_root_uri: str | None = None,
        latest_validation_run_id: str | None = None,
        validation_status: DatasetValidationStatus | None = None,
        should_block_pipeline: bool | None = None,
        validation_report_uri: str | None = None,
        latest_profile_run_id: str | None = None,
        profile_report_uri: str | None = None,
    ) -> DatasetVersionRecord:
        return await self._versions.update_scene_summary(
            dataset_id=dataset_id,
            version=version,
            scene_count=scene_count,
            sample_count=sample_count,
            frame_count=frame_count,
            channels=channels,
            required_channels=required_channels,
            manifest_uri=manifest_uri,
            raw_source_root_uri=raw_source_root_uri,
            latest_validation_run_id=latest_validation_run_id,
            validation_status=validation_status,
            should_block_pipeline=should_block_pipeline,
            validation_report_uri=validation_report_uri,
            latest_profile_run_id=latest_profile_run_id,
            profile_report_uri=profile_report_uri,
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
