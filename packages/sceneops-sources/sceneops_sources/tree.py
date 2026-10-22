"""Reading a local source tree: which files a selection preserves, and their
inventory (ADR-015 §5).

The tree is read as regular files and directories only. Symbolic links are
never followed, and a selected path that is not a regular file fails, so the
preserved bytes are exactly the files under the source root and a link can
never pull bytes in from elsewhere. Nothing here interprets a file.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from sceneops_core.sources import (
    SnapshotSelection,
    SourceContentInventory,
    SourceFileEntry,
    SourceSelectionError,
    validate_source_path,
)

_HASH_CHUNK_BYTES: Final = 1 << 20


class SourceTreeError(RuntimeError):
    """The local source tree cannot be preserved as declared."""


class SourceFileChangedError(SourceTreeError):
    """A source file changed while it was being preserved."""


def _candidates(source_dir: Path) -> dict[str, bool]:
    """Every entry below ``source_dir`` that a pattern could select, by
    relative ``/`` path, mapped to whether it is a regular file. A symlinked
    directory is an entry itself and is not descended into."""

    def _raise(error: OSError) -> None:
        raise SourceTreeError(f"cannot read the source tree: {error}") from error

    entries: dict[str, bool] = {}
    for directory, dirnames, filenames in os.walk(source_dir, onerror=_raise):
        relative = Path(directory).relative_to(source_dir)
        # os.walk lists a symlink to a directory among dirnames without
        # following it; lstat records it here as a non-regular entry.
        for name in (*dirnames, *filenames):
            mode = os.lstat(Path(directory) / name).st_mode
            if stat.S_ISDIR(mode):
                continue
            entries[(relative / name).as_posix()] = stat.S_ISREG(mode)
    return entries


def select_files(source_dir: Path, selection: SnapshotSelection) -> tuple[str, ...]:
    """The relative paths of the files ``selection`` preserves from
    ``source_dir``, sorted. Fails when a pattern matches nothing, when a
    selected entry is not a regular file, or when a selected path has no
    single canonical spelling."""
    if not source_dir.is_dir():
        raise SourceTreeError(f"source root is not a directory: {source_dir}")
    entries = _candidates(source_dir)
    try:
        selected = selection.select(entries)
    except SourceSelectionError as exc:
        raise SourceTreeError(f"{exc} (source root {source_dir})") from exc
    not_regular = [path for path in selected if not entries[path]]
    if not_regular:
        raise SourceTreeError(
            f"selected entries are not regular files (links are never followed): "
            f"{not_regular[:10]}"
        )
    for path in selected:
        try:
            validate_source_path(path)
        except ValueError as exc:
            raise SourceTreeError(f"selected path cannot be preserved: {exc}") from exc
    return selected


def hash_file(path: Path) -> tuple[int, str]:
    """``(size_bytes, sha256 checksum)`` of the file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return size, f"sha256:{digest.hexdigest()}"


def _regular_file_stat(path: Path) -> os.stat_result:
    try:
        status = os.lstat(path)
    except FileNotFoundError as exc:
        raise SourceFileChangedError(f"source file disappeared: {path}") from exc
    if not stat.S_ISREG(status.st_mode):
        raise SourceTreeError(f"not a regular file: {path}")
    return status


def build_inventory(source_dir: Path, paths: Iterable[str]) -> SourceContentInventory:
    """Hash every selected file into the content inventory.

    A file whose size or modification time differs before and after hashing
    fails: the inventory never records bytes that were not stable while they
    were read."""
    entries = []
    for relative in paths:
        path = source_dir / relative
        before = _regular_file_stat(path)
        size, checksum = hash_file(path)
        after = _regular_file_stat(path)
        if size != before.st_size or (after.st_size, after.st_mtime_ns) != (
            before.st_size,
            before.st_mtime_ns,
        ):
            raise SourceFileChangedError(f"source file changed while hashing: {path}")
        entries.append(SourceFileEntry(path=relative, size_bytes=size, sha256=checksum))
    if not entries:
        raise SourceTreeError("an inventory needs at least one file")
    return SourceContentInventory.create(entries)


def read_inventory_file(source_dir: Path, entry: SourceFileEntry) -> bytes:
    """The bytes of one inventoried file, required to still be exactly the
    bytes the inventory records."""
    path = source_dir / entry.path
    _regular_file_stat(path)
    data = path.read_bytes()
    if len(data) != entry.size_bytes or (
        f"sha256:{hashlib.sha256(data).hexdigest()}" != entry.sha256
    ):
        raise SourceFileChangedError(
            f"source file no longer matches its inventory entry: {path}"
        )
    return data


__all__ = [
    "SourceFileChangedError",
    "SourceTreeError",
    "build_inventory",
    "hash_file",
    "read_inventory_file",
    "select_files",
]
