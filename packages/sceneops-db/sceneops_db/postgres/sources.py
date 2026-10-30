"""Insert-if-absent registration of SourceContent / SourceSnapshot records
(contract: ``sceneops_db.repositories.sources``)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.sources import (
    InconsistentSourceRegistryError,
    SourceContentRecord,
    SourceSnapshotRecord,
    decide_content_registration,
    decide_snapshot_registration,
)

from sceneops_db.converters.sources import (
    source_content_model_to_record,
    source_content_record_to_values,
    source_snapshot_model_to_record,
    source_snapshot_record_to_values,
)
from sceneops_db.models.sources import SourceContentModel, SourceSnapshotModel
from sceneops_db.repositories.sources import SourceRegistration

from ._utils import apply_pagination


class PostgresSourceRegistryRepository:
    """Insert-only: Source Registry rows are immutable (PostgreSQL rejects
    UPDATE on both tables and DELETE of a snapshot)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_content(self, content_id: str) -> SourceContentRecord | None:
        model = (
            await self._session.execute(
                select(SourceContentModel).where(
                    SourceContentModel.content_id == content_id
                )
            )
        ).scalar_one_or_none()
        return source_content_model_to_record(model) if model is not None else None

    async def get_snapshot(self, snapshot_id: str) -> SourceSnapshotRecord | None:
        model = (
            await self._session.execute(
                select(SourceSnapshotModel).where(
                    SourceSnapshotModel.snapshot_id == snapshot_id
                )
            )
        ).scalar_one_or_none()
        return source_snapshot_model_to_record(model) if model is not None else None

    async def list_snapshots(
        self,
        *,
        content_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceSnapshotRecord]:
        stmt = select(SourceSnapshotModel)
        if content_id is not None:
            stmt = stmt.where(SourceSnapshotModel.content_id == content_id)
        stmt = apply_pagination(
            stmt.order_by(
                SourceSnapshotModel.registered_at, SourceSnapshotModel.snapshot_id
            ),
            limit=limit,
            offset=offset,
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [source_snapshot_model_to_record(m) for m in models]

    async def _insert(self, model: type, values: dict, key_column) -> bool:
        inserted = (
            await self._session.execute(
                insert(model)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(key_column)
            )
        ).scalar_one_or_none()
        return inserted is not None

    async def register(
        self, *, content: SourceContentRecord, snapshot: SourceSnapshotRecord
    ) -> SourceRegistration:
        if (snapshot.content_id, snapshot.format) != (
            content.content_id,
            content.format,
        ):
            raise ValueError(
                f"snapshot {snapshot.snapshot_id} declares "
                f"({snapshot.content_id}, {snapshot.format}), not the candidate "
                f"content ({content.content_id}, {content.format})"
            )
        content_inserted = await self._insert(
            SourceContentModel,
            source_content_record_to_values(content),
            SourceContentModel.content_id,
        )
        stored_content = await self.get_content(content.content_id)
        if stored_content is None:
            # The insert conflicted on another unique key of the content
            # (its checksum or inventory record) under a different id.
            raise InconsistentSourceRegistryError(
                f"content {content.content_id} conflicts with a registered content "
                "under another id"
            )
        content_outcome = decide_content_registration(
            content, None if content_inserted else stored_content
        )

        snapshot_inserted = await self._insert(
            SourceSnapshotModel,
            source_snapshot_record_to_values(snapshot),
            SourceSnapshotModel.snapshot_id,
        )
        stored_snapshot = await self.get_snapshot(snapshot.snapshot_id)
        if stored_snapshot is None:
            raise InconsistentSourceRegistryError(
                f"snapshot {snapshot.snapshot_id} conflicts with a registered "
                "snapshot under another id"
            )
        snapshot_outcome = decide_snapshot_registration(
            snapshot, None if snapshot_inserted else stored_snapshot
        )
        return SourceRegistration(
            content=stored_content,
            snapshot=stored_snapshot,
            content_outcome=content_outcome,
            snapshot_outcome=snapshot_outcome,
        )


__all__ = ["PostgresSourceRegistryRepository"]
