from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.sources import SourceContentRecord, SourceSnapshotRecord
from sceneops_db.postgres import PostgresSourceRegistryRepository
from sceneops_db.repositories import SourceRegistration


class SourceStore:
    """The Source Registry (ADR-015 §5): insert-only content and snapshot
    records, written only by REGISTER_SOURCE_SNAPSHOT."""

    def __init__(self, session: AsyncSession) -> None:
        self._registry = PostgresSourceRegistryRepository(session)

    async def get_content(self, content_id: str) -> SourceContentRecord | None:
        return await self._registry.get_content(content_id)

    async def get_snapshot(self, snapshot_id: str) -> SourceSnapshotRecord | None:
        return await self._registry.get_snapshot(snapshot_id)

    async def register(
        self, *, content: SourceContentRecord, snapshot: SourceSnapshotRecord
    ) -> SourceRegistration:
        """Inserts what is absent and decides what exists, in the caller's
        transaction; raises SourceRegistrationError on a conflict."""
        return await self._registry.register(content=content, snapshot=snapshot)
