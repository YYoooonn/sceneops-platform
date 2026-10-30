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


class DatasetVersionExistsError(RuntimeError):
    """``(dataset_id, version)`` is already bound to a composition (or a
    concurrent composition of it committed first). Nothing was written."""

    def __init__(self, dataset_id: str, version: str) -> None:
        super().__init__(f"DatasetVersion already exists: {dataset_id}/{version}")
        self.dataset_id = dataset_id
        self.version = version


@runtime_checkable
class DatasetVersionRepository(Protocol):
    """Committed DatasetVersions (ADR-015 §7, MS-8). ``insert`` is the only
    write and belongs to COMPOSE_DATASET_VERSION: it adds one complete
    composition, the version row and all its members, in the caller's
    transaction. No method updates or deletes a version or its members."""

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]: ...

    async def insert(
        self,
        version: DatasetVersionRecord,
        members: Sequence[DatasetVersionMember],
    ) -> DatasetVersionRecord:
        """Raises ``DatasetVersionExistsError`` when the name is taken; an
        uncommitted concurrent insert of it is waited for first. Members
        must be non-empty and agree with the version's summary counts."""
        ...


@runtime_checkable
class DatasetVersionMemberRepository(Protocol):
    """The revision pins of a committed DatasetVersion (ADR-015 §7), in
    unit id order. Members are written only with their version
    (``DatasetVersionRepository.insert``) and never changed."""

    async def list(
        self,
        *,
        dataset_id: str,
        version: str,
        domain: CanonicalDomain | None = None,
    ) -> list[DatasetVersionMember]: ...
