from __future__ import annotations

from typing import Protocol

from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import (
    DatasetRecord,
    DatasetVersionRecord,
    DatasetVersionStatus,
)


class DatasetRepository(Protocol):
    async def create(self, record: DatasetRecord) -> DatasetRecord:
        ...

    async def get(self, dataset_id: str) -> DatasetRecord:
        ...

    async def list(self) -> list[DatasetRecord]:
        ...

    async def update(self, record: DatasetRecord) -> DatasetRecord:
        ...

    async def upsert(
        self,
        *,
        dataset_id: str,
        name: str | None = None,
        dataset_type: str,
        description: str | None = None,
        metadata: JsonDict | None = None,
    ) -> DatasetRecord:
        ...


class DatasetVersionRepository(Protocol):
    async def create(self, record: DatasetVersionRecord) -> DatasetVersionRecord:
        ...

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord:
        ...

    async def list(
        self,
        *,
        dataset_id: str,
    ) -> list[DatasetVersionRecord]:
        ...

    async def update(self, record: DatasetVersionRecord) -> DatasetVersionRecord:
        ...

    async def upsert(
        self,
        *,
        dataset_id: str,
        version: str,
        dataset_type: str,
        manifest_uri: str | None = None,
        source_uri: str | None = None,
        scene_count: int | None = None,
        sample_count: int | None = None,
        annotation_count: int | None = None,
        status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED,
        metadata: JsonDict | None = None,
    ) -> DatasetVersionRecord:
        ...
