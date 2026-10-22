"""Database-free snapshot publication (ADR-015 §5).

Turns a local source tree, one declaration and its annotations into the
preserved objects of the source layout (``sceneops_sources.layout``)::

    1  select the declared files and hash them      -> inventory -> content_id
       build the descriptor                          -> snapshot_id
    2  refuse a declaration already published with another descriptor,
       before anything is written
    3  upload each file under content_id, write-once (bytes already there are
       reused: deduplication)
    4  write the inventory                           write-once
    5  write the descriptor LAST                     no descriptor = not published

Ownership: this module writes object bytes only. It never opens a database
session and creates no ArtifactRecord; ``REGISTER_SOURCE_SNAPSHOT`` verifies
what was published here and registers it. Retrieval facts (local path, time,
operator) belong to the caller's logs, never to an object.

Failure semantics:

- A crash before step 5 leaves no descriptor, so nothing is published. Files
  and an inventory without a descriptor are orphan candidates under the
  content prefix and are reused by a retry, because their keys are derived
  from their bytes.
- A retry with the same tree and declaration converges: every write finds
  identical bytes and reports itself as reused.
- One declaration with other bytes or other annotations is a conflict. It is
  refused in step 2 when the earlier descriptor is already published, and by
  the write-once descriptor key when two publishers race.
- A source file that changes between hashing and upload fails publication;
  the inventory never names bytes other than those uploaded.
- Content files are format-free, so the same bytes declared under another
  format publish to the same content; registration refuses that conflict.

Whole files are read into memory for upload (M3.0 D12).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.sources import (
    SnapshotDeclaration,
    SourceContentInventory,
    SourceSnapshotDescriptor,
    SourceSnapshotError,
    load_canonical_source_snapshot_descriptor,
)
from sceneops_storage import WriteOnceConflictError, write_once

from .layout import content_file_uri, descriptor_uri, inventory_uri
from .tree import build_inventory, read_inventory_file, select_files


class SnapshotPublicationConflictError(WriteOnceConflictError):
    """The declaration is already published with another descriptor: other
    bytes (content) or other annotations."""


class ContentPublicationConflictError(WriteOnceConflictError):
    """A content key holds bytes other than the ones its id was derived
    from: the stored content is corrupt."""


@dataclass(frozen=True)
class PublishedSnapshot:
    descriptor: SourceSnapshotDescriptor
    inventory: SourceContentInventory
    descriptor_uri: str
    inventory_uri: str
    files_written: int
    files_reused: int
    inventory_written: bool
    descriptor_written: bool

    @property
    def snapshot_id(self) -> str:
        return self.descriptor.snapshot_id

    @property
    def content_id(self) -> str:
        return self.inventory.content_id

    @property
    def descriptor_checksum(self) -> str:
        return self.descriptor.descriptor_checksum

    @property
    def wrote_anything(self) -> bool:
        return bool(
            self.files_written or self.inventory_written or self.descriptor_written
        )


def _describe_conflict(existing: bytes, new: SourceSnapshotDescriptor, uri: str) -> str:
    try:
        published = load_canonical_source_snapshot_descriptor(existing)
    except SourceSnapshotError:
        return f"{uri} holds bytes that are not a canonical descriptor"
    differences = []
    if published.content_id != new.content_id:
        differences.append(
            f"content {published.content_id} published, {new.content_id} now "
            "(one declared revision, two byte sets)"
        )
    if published.annotations != new.annotations:
        differences.append(
            f"annotations {published.annotations} published, {new.annotations} now"
        )
    if published.declaration != new.declaration:
        differences.append("another declaration under this snapshot id")
    detail = "; ".join(differences) or "other descriptor bytes"
    return f"snapshot {new.snapshot_id} is already published differently: {detail}"


async def _refuse_conflicting_descriptor(
    store: ArtifactStore, uri: str, descriptor: SourceSnapshotDescriptor, data: bytes
) -> None:
    if not await store.exists(uri):
        return
    existing = await store.read_bytes(uri)
    if existing != data:
        raise SnapshotPublicationConflictError(
            _describe_conflict(existing, descriptor, uri)
        )


async def publish_snapshot(
    *,
    store: ArtifactStore,
    sources_root: str,
    source_dir: Path,
    declaration: SnapshotDeclaration,
    annotations: Mapping[str, str] | None = None,
) -> PublishedSnapshot:
    """Publish the files ``declaration`` selects from ``source_dir``.
    Idempotent: identical inputs reuse every object and return the same
    descriptor bytes."""
    # 1 -- select, hash, identify.
    paths = select_files(source_dir, declaration.selection)
    inventory = build_inventory(source_dir, paths)
    descriptor = SourceSnapshotDescriptor(
        declaration=declaration,
        content_id=inventory.content_id,
        annotations=dict(annotations or {}),
    )
    descriptor_bytes = descriptor.to_canonical_bytes()
    target_inventory = inventory_uri(store, sources_root, inventory.content_id)
    target_descriptor = descriptor_uri(store, sources_root, descriptor.snapshot_id)

    # 2 -- an inconsistent declaration writes nothing.
    await _refuse_conflicting_descriptor(
        store, target_descriptor, descriptor, descriptor_bytes
    )

    # 3 -- content files, once per content.
    written = reused = 0
    for entry in inventory.files:
        result = await write_once(
            store,
            content_file_uri(target_inventory, entry.path),
            read_inventory_file(source_dir, entry),
            conflict=ContentPublicationConflictError,
        )
        if result.created:
            written += 1
        else:
            reused += 1

    # 4 -- the inventory.
    inventory_result = await write_once(
        store,
        target_inventory,
        inventory.to_canonical_bytes(),
        conflict=ContentPublicationConflictError,
    )

    # 5 -- the descriptor last: it is the publication marker.
    try:
        descriptor_result = await write_once(
            store,
            target_descriptor,
            descriptor_bytes,
            conflict=SnapshotPublicationConflictError,
        )
    except SnapshotPublicationConflictError:
        # A concurrent publisher of the same declaration won with other bytes.
        existing = await store.read_bytes(target_descriptor)
        raise SnapshotPublicationConflictError(
            _describe_conflict(existing, descriptor, target_descriptor)
        ) from None

    return PublishedSnapshot(
        descriptor=descriptor,
        inventory=inventory,
        descriptor_uri=target_descriptor,
        inventory_uri=target_inventory,
        files_written=written,
        files_reused=reused,
        inventory_written=inventory_result.created,
        descriptor_written=descriptor_result.created,
    )


__all__ = [
    "ContentPublicationConflictError",
    "PublishedSnapshot",
    "SnapshotPublicationConflictError",
    "publish_snapshot",
]
