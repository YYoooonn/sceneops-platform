from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord


@runtime_checkable
class DatasetRepository(Protocol):
    async def create(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def upsert(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def get(self, dataset_id: str) -> DatasetRecord | None: ...

    async def update(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def list(
        self,
        *,
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

    async def lock_for_update(
        self, *, dataset_id: str, version: str
    ) -> DatasetVersionRecord: ...

    async def replace_scene_membership_summary(
        self,
        *,
        dataset_id: str,
        version: str,
        scene_count: int,
        keyframe_count: int,
        observation_count: int,
        observed_channels: list[str],
    ) -> DatasetVersionRecord: ...

    async def update_scene_inputs(
        self,
        *,
        dataset_id: str,
        version: str,
        required_channels: list[str] | None = None,
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
