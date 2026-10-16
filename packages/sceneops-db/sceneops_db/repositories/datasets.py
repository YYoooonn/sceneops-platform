from __future__ import annotations

from typing import Protocol, runtime_checkable

from collections.abc import Sequence

from sceneops_core.datasets.schemas import (
    DatasetRecord,
    DatasetVersionMember,
    DatasetVersionRecord,
)
from sceneops_core.provenance import CanonicalDomain


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

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]: ...


@runtime_checkable
class DatasetVersionMemberRepository(Protocol):
    """The revision pins of a DatasetVersion (ADR-015 §7). A member is never
    updated or removed; ``add`` refuses a second member for a unit already in the
    version and a pin that names no registered revision (foreign key)."""

    async def list(
        self,
        *,
        dataset_id: str,
        version: str,
        domain: CanonicalDomain | None = None,
    ) -> list[DatasetVersionMember]: ...

    async def add(
        self,
        *,
        dataset_id: str,
        version: str,
        members: Sequence[DatasetVersionMember],
    ) -> None: ...
