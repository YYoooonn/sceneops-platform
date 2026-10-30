"""Verified access to the files of one SourceContent (ADR-015 §5, §6.5).

``ContentFiles`` is the only way format readers and canonical builders reach
preserved bytes. Every path it hands out names a local regular file whose
size and sha256 equal the content inventory entry, checked before the path
is returned. A path the inventory does not list is refused, never read.

- ``LocalContentFiles``: a local directory checked against an inventory
  (tests, tools).
- ``StoredContentFiles``: the files materialized from the object store into a
  caller-owned work directory. The inventory is read from its stored object
  and accepted only when its ``content_checksum`` is the pinned one (from the
  registered content), so the stored inventory cannot redefine the content.

``verify_stored_content`` is the check behind a content's first
registration: the stored files are exactly the inventory, byte for byte.

A verified path stays valid only while nothing else writes the directory:
the work directory belongs to one build.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.sources import (
    SourceContentError,
    SourceContentInventory,
    SourceFileEntry,
    load_canonical_source_content_inventory,
)
from sceneops_storage import ArtifactNotFoundError

from .layout import content_file_uri, content_files_prefix, content_id_of_inventory_uri
from .tree import hash_file


class ContentFileError(RuntimeError):
    """A content file cannot be provided with the bytes its inventory pins."""


class ContentFileMissingError(ContentFileError):
    """A file the inventory lists is not present."""


class ContentFileIntegrityError(ContentFileError):
    """Present bytes differ from the inventory (size or sha256), or the
    inventory itself is not the pinned one."""


class ContentFiles(Protocol):
    @property
    def inventory(self) -> SourceContentInventory: ...

    def local_path(self, path: str) -> Path:
        """A local regular file with exactly the inventory's bytes for
        ``path``; raises ``ContentFileError`` otherwise."""
        ...


def _entries_by_path(inventory: SourceContentInventory) -> dict[str, SourceFileEntry]:
    return {entry.path: entry for entry in inventory.files}


def _entry(
    entries: dict[str, SourceFileEntry], path: str, content_id: str
) -> SourceFileEntry:
    entry = entries.get(path)
    if entry is None:
        raise ContentFileMissingError(f"{path!r} is not a file of {content_id}")
    return entry


def _verify_local_file(path: Path, entry: SourceFileEntry) -> None:
    if path.is_symlink() or not path.is_file():
        if not path.exists() and not path.is_symlink():
            raise ContentFileMissingError(f"content file is missing: {path}")
        raise ContentFileIntegrityError(f"content file is not a regular file: {path}")
    if path.stat().st_size != entry.size_bytes:
        raise ContentFileIntegrityError(
            f"{path} has {path.stat().st_size} bytes, the inventory pins "
            f"{entry.size_bytes}"
        )
    size, checksum = hash_file(path)
    if (size, checksum) != (entry.size_bytes, entry.sha256):
        raise ContentFileIntegrityError(
            f"{path} has sha256 {checksum}, the inventory pins {entry.sha256}"
        )


class LocalContentFiles:
    """A local directory whose files are checked, on first access, against
    the inventory."""

    def __init__(self, root: Path, inventory: SourceContentInventory) -> None:
        self._root = root
        self._inventory = inventory
        self._entries = _entries_by_path(inventory)
        self._verified: set[str] = set()

    @property
    def inventory(self) -> SourceContentInventory:
        return self._inventory

    def local_path(self, path: str) -> Path:
        entry = _entry(self._entries, path, self._inventory.content_id)
        local = self._root / entry.path
        if path not in self._verified:
            _verify_local_file(local, entry)
            self._verified.add(path)
        return local


async def load_stored_inventory(
    store: ArtifactStore, inventory_uri: str, *, content_checksum: str
) -> SourceContentInventory:
    """The stored inventory, strictly parsed, at its own content's key, and
    with exactly the pinned ``content_checksum``."""
    key_content_id = content_id_of_inventory_uri(inventory_uri)
    try:
        data = await store.read_bytes(inventory_uri)
    except ArtifactNotFoundError as exc:
        raise ContentFileMissingError(f"inventory is missing: {inventory_uri}") from exc
    try:
        inventory = load_canonical_source_content_inventory(data)
    except SourceContentError as exc:
        raise ContentFileIntegrityError(f"{inventory_uri}: {exc}") from exc
    if inventory.content_checksum != content_checksum:
        raise ContentFileIntegrityError(
            f"{inventory_uri} has content_checksum {inventory.content_checksum}, "
            f"the pinned content has {content_checksum}"
        )
    if inventory.content_id != key_content_id:
        raise ContentFileIntegrityError(
            f"{inventory_uri} holds the inventory of {inventory.content_id}"
        )
    return inventory


async def verify_stored_content(
    store: ArtifactStore,
    *,
    inventory_uri: str,
    inventory: SourceContentInventory,
    read_concurrency: int = 8,
) -> None:
    """Require the stored files of a content to be exactly its inventory.

    One listing of the content's files prefix must hold one object per
    inventory entry with the entry's size and nothing else; then every object
    is read back and its sha256 compared. Raises ``ContentFileMissingError``
    or ``ContentFileIntegrityError`` naming the first offending files. Whole
    objects are read into memory (M3.0 D12), at most ``read_concurrency`` at
    a time."""
    prefix = content_files_prefix(inventory_uri)
    expected = {
        content_file_uri(inventory_uri, entry.path): entry for entry in inventory.files
    }
    listed = {obj.uri: obj.size_bytes for obj in await store.list_objects(prefix)}
    missing = sorted(set(expected) - set(listed))
    if missing:
        raise ContentFileMissingError(
            f"{len(missing)} file(s) of {inventory.content_id} are not stored, "
            f"e.g. {missing[:5]}"
        )
    unexpected = sorted(set(listed) - set(expected))
    wrong_size = sorted(
        uri for uri, entry in expected.items() if listed[uri] != entry.size_bytes
    )
    if unexpected or wrong_size:
        raise ContentFileIntegrityError(
            f"stored files of {inventory.content_id} differ from the inventory: "
            f"{len(wrong_size)} with another size (e.g. {wrong_size[:5]}), "
            f"{len(unexpected)} not inventoried (e.g. {unexpected[:5]})"
        )

    semaphore = asyncio.Semaphore(read_concurrency)

    async def _checksum_mismatch(uri: str, entry: SourceFileEntry) -> str | None:
        async with semaphore:
            data = await store.read_bytes(uri)
        if len(data) != entry.size_bytes or sha256_checksum(data) != entry.sha256:
            return uri
        return None

    mismatched = sorted(
        uri
        for uri in await asyncio.gather(
            *(_checksum_mismatch(uri, entry) for uri, entry in expected.items())
        )
        if uri is not None
    )
    if mismatched:
        raise ContentFileIntegrityError(
            f"{len(mismatched)} stored file(s) of {inventory.content_id} differ "
            f"from their inventory sha256, e.g. {mismatched[:5]}"
        )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class StoredContentFiles:
    """Content files materialized from the object store into ``workdir``.

    Each file is read from its content key and written to ``workdir`` only
    after its size and sha256 equal the inventory entry, through a temporary
    name and an atomic rename, so an interrupted materialization never leaves
    a wrong file at a final path. A file already present in ``workdir`` (an
    earlier attempt) is re-verified and never overwritten: a mismatch fails.
    """

    def __init__(
        self,
        *,
        inventory_uri: str,
        inventory: SourceContentInventory,
        workdir: Path,
        materialized: Iterable[str],
    ) -> None:
        self._inventory_uri = inventory_uri
        self._inventory = inventory
        self._entries = _entries_by_path(inventory)
        self._workdir = workdir
        self._materialized = frozenset(materialized)

    @property
    def inventory(self) -> SourceContentInventory:
        return self._inventory

    @property
    def inventory_uri(self) -> str:
        return self._inventory_uri

    @classmethod
    async def materialize(
        cls,
        *,
        store: ArtifactStore,
        inventory_uri: str,
        content_checksum: str,
        workdir: Path,
        paths: Iterable[str] | None = None,
    ) -> StoredContentFiles:
        """Materialize ``paths`` (default: every file of the content)."""
        inventory = await load_stored_inventory(
            store, inventory_uri, content_checksum=content_checksum
        )
        entries = _entries_by_path(inventory)
        wanted = sorted(set(entries if paths is None else paths))
        root = workdir.resolve()
        for path in wanted:
            entry = _entry(entries, path, inventory.content_id)
            local = root / entry.path
            if not local.resolve().is_relative_to(root):
                raise ContentFileIntegrityError(f"{local} resolves outside {root}")
            if local.exists() or local.is_symlink():
                _verify_local_file(local, entry)
                continue
            uri = content_file_uri(inventory_uri, entry.path)
            try:
                data = await store.read_bytes(uri)
            except ArtifactNotFoundError as exc:
                raise ContentFileMissingError(
                    f"content file is missing: {uri}"
                ) from exc
            if len(data) != entry.size_bytes or sha256_checksum(data) != entry.sha256:
                raise ContentFileIntegrityError(
                    f"{uri} holds {len(data)} bytes with sha256 {sha256_checksum(data)}; "
                    f"the inventory pins {entry.size_bytes} bytes with {entry.sha256}"
                )
            _atomic_write(local, data)
        return cls(
            inventory_uri=inventory_uri,
            inventory=inventory,
            workdir=root,
            materialized=wanted,
        )

    def local_path(self, path: str) -> Path:
        entry = _entry(self._entries, path, self._inventory.content_id)
        if path not in self._materialized:
            raise ContentFileMissingError(
                f"{path!r} of {self._inventory.content_id} was not materialized"
            )
        return self._workdir / entry.path


__all__ = [
    "ContentFileError",
    "ContentFileIntegrityError",
    "ContentFileMissingError",
    "ContentFiles",
    "LocalContentFiles",
    "StoredContentFiles",
    "load_stored_inventory",
    "verify_stored_content",
]
