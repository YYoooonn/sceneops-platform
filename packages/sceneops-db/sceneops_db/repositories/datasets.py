from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.enums import DatasetType
from sceneops_core.datasets.schemas.validation import DatasetValidationStatus


@runtime_checkable
class DatasetRepository(Protocol):
    async def create(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def upsert(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def get(self, dataset_id: str) -> DatasetRecord | None: ...

    async def update(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def list(
        self,
        *,
        type: DatasetType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRecord]: ...


@runtime_checkable
class DatasetVersionRepository(Protocol):
    async def create(self, version: DatasetVersionRecord) -> DatasetVersionRecord: ...

    async def upsert(self, version: DatasetVersionRecord) -> DatasetVersionRecord: ...

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None: ...

    async def update(self, version: DatasetVersionRecord) -> DatasetVersionRecord: ...

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
    ) -> DatasetVersionRecord: ...

    async def update_episode_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        episode_count: int | None = None,
    ) -> DatasetVersionRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]: ...
