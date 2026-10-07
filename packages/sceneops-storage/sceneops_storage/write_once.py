"""Write-once publication of immutable artifact bytes.

An object key that names content (a checksum-qualified name, or an id derived
from the inputs) is published at most once. Publishing the same bytes again is
a retry or a concurrent writer converging on the same object and is a no-op;
different bytes under an existing key are a conflict and never replace the
object, so bytes a database record has pinned cannot change underneath it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.common.checksums import sha256_checksum

from sceneops_storage.exceptions import ArtifactStoreError


class WriteOnceConflictError(ArtifactStoreError):
    """A write-once key already holds different bytes."""


class WriteOnceIntegrityError(ArtifactStoreError):
    """Bytes read back from a freshly written key differ from what was written."""


@dataclass(frozen=True)
class WrittenObject:
    uri: str
    checksum: str
    size_bytes: int
    created: bool


async def write_once(
    store: ArtifactStore,
    uri: str,
    data: bytes,
    *,
    conflict: type[WriteOnceConflictError] = WriteOnceConflictError,
    verify: bool = True,
) -> WrittenObject:
    """Publish ``data`` at ``uri`` unless the key exists.

    Two writers of one key race only when they hold the same content (the key
    names it), so the window between ``exists`` and ``write_bytes`` can at
    worst write identical bytes twice. ``verify`` reads the object back after
    a fresh write; callers publishing large objects whose own checksum is
    verified on every pinned read may skip it."""
    checksum = sha256_checksum(data)
    if await store.exists(uri):
        if await store.read_bytes(uri) != data:
            raise conflict(
                f"{uri} already holds different bytes; the key is write-once"
            )
        return WrittenObject(uri, checksum, len(data), created=False)
    await store.write_bytes(uri, data)
    if verify and await store.read_bytes(uri) != data:
        raise WriteOnceIntegrityError(
            f"read-back of {uri} differs from what was written"
        )
    return WrittenObject(uri, checksum, len(data), created=True)


__all__ = [
    "WriteOnceConflictError",
    "WriteOnceIntegrityError",
    "WrittenObject",
    "write_once",
]
