"""The persistence contract of the Source Registry (ADR-015 §5, MS-6a).

``register(content, snapshot)`` inserts the records of one registration in
the caller's transaction, each only if absent, and decides each against the
row that is then stored (``decide_content_registration``,
``decide_snapshot_registration``):

    content    inserted, or an existing row of the same format is reused
    snapshot   inserted, or an existing row with the same content and
               descriptor is the same registration (no-op)
    anything else raises a SourceRegistrationError; nothing is decided on top

Rows are never updated or deleted. Concurrency is decided by the primary
keys, not by locks: inserts are ``ON CONFLICT DO NOTHING``, which waits for
a concurrent uncommitted insert of the same id, and the loser re-reads the
committed row (READ COMMITTED) and decides against it, so identical
registrations converge and conflicting ones fail.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sceneops_core.sources import (
    RegistrationOutcome,
    SourceContentRecord,
    SourceSnapshotRecord,
)


@dataclass(frozen=True)
class SourceRegistration:
    # The stored records (for EXISTING, the ones already registered).
    content: SourceContentRecord
    snapshot: SourceSnapshotRecord
    content_outcome: RegistrationOutcome
    snapshot_outcome: RegistrationOutcome


@runtime_checkable
class SourceRegistryRepository(Protocol):
    async def get_content(self, content_id: str) -> SourceContentRecord | None: ...

    async def get_snapshot(self, snapshot_id: str) -> SourceSnapshotRecord | None: ...

    async def list_snapshots(
        self,
        *,
        content_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[SourceSnapshotRecord]: ...

    async def register(
        self, *, content: SourceContentRecord, snapshot: SourceSnapshotRecord
    ) -> SourceRegistration: ...
