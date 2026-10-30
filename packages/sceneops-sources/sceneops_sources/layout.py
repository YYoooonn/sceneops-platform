"""Object layout of preserved sources (ADR-015 §5; M3.0 D4, D11).

Under one sources root (by default ``{ARTIFACT_ROOT_URI}/sources``)::

    contents/{content_id}/files/{path}            each preserved file, write-once
    contents/{content_id}/inventory.json          SourceContent inventory, write-once
    snapshots/{snapshot_id}/descriptor.json       SourceSnapshot descriptor, written last

Every key names immutable content: the content id is derived from the bytes
and the snapshot id from the declaration, so files are stored once per content
however many snapshots declare it. A content file's location is derived from
its inventory's location (D4), and a descriptor's location names its root and
its snapshot (D11), so a reader needs no root setting of its own.

Canonical manifests reference a preserved file in place, by the Logical URI of
its key relative to the sources root (``content_file_payload_ref``, ADR-016);
registration accepts that URI only when it resolves to exactly where the
content's registered inventory places the file.
"""

from __future__ import annotations

from typing import Final

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.artifacts.schemas import PayloadRef, PayloadSelector
from sceneops_core.sources import (
    SourceFileEntry,
    validate_content_id,
    validate_snapshot_id,
    validate_source_path,
)

CONTENTS_SEGMENT: Final = "contents"
SNAPSHOTS_SEGMENT: Final = "snapshots"
FILES_SEGMENT: Final = "files"
INVENTORY_OBJECT_NAME: Final = "inventory.json"
DESCRIPTOR_OBJECT_NAME: Final = "descriptor.json"


class SourceLayoutError(ValueError):
    """A URI is not where the source layout puts that object."""


def content_root_uri(store: ArtifactStore, sources_root: str, content_id: str) -> str:
    validate_content_id(content_id)
    return store.join_uri(sources_root, CONTENTS_SEGMENT, content_id)


def inventory_uri(store: ArtifactStore, sources_root: str, content_id: str) -> str:
    return store.join_uri(
        content_root_uri(store, sources_root, content_id), INVENTORY_OBJECT_NAME
    )


def descriptor_uri(store: ArtifactStore, sources_root: str, snapshot_id: str) -> str:
    return store.join_uri(
        sources_root, SNAPSHOTS_SEGMENT, snapshot_id, DESCRIPTOR_OBJECT_NAME
    )


def _split_parent(uri: str, name: str) -> tuple[str, str]:
    """``(parent, last segment)`` of the prefix that holds ``name``."""
    suffix = f"/{name}"
    if not uri.endswith(suffix):
        raise SourceLayoutError(f"{uri} does not end in {suffix}")
    parent = uri[: -len(suffix)]
    head, _, segment = parent.rpartition("/")
    if not head or not segment:
        raise SourceLayoutError(f"{uri} has no parent prefix for {name}")
    return head, segment


def content_id_of_inventory_uri(uri: str) -> str:
    """The content id an inventory URI names; it must be
    ``.../contents/{content_id}/inventory.json``."""
    head, content_id = _split_parent(uri, INVENTORY_OBJECT_NAME)
    if not head.endswith(f"/{CONTENTS_SEGMENT}"):
        raise SourceLayoutError(f"{uri} is not under a {CONTENTS_SEGMENT}/ prefix")
    try:
        return validate_content_id(content_id)
    except ValueError as exc:
        raise SourceLayoutError(f"{uri}: {exc}") from exc


def content_file_uri(inventory_uri: str, path: str) -> str:
    """Where a content file is stored: ``dirname(inventory)/files/{path}``
    (D4). The path is validated, so it never leaves the content prefix."""
    content_id_of_inventory_uri(inventory_uri)
    validate_source_path(path)
    prefix = inventory_uri[: -len(INVENTORY_OBJECT_NAME)]
    return f"{prefix}{FILES_SEGMENT}/{path}"


def content_files_prefix(inventory_uri: str) -> str:
    """The prefix holding every file of one content, for presence listings."""
    content_id_of_inventory_uri(inventory_uri)
    return f"{inventory_uri[: -len(INVENTORY_OBJECT_NAME)]}{FILES_SEGMENT}"


def content_file_key(content_id: str, path: str) -> str:
    """A content file's object key relative to the sources root,
    ``contents/{content_id}/files/{path}``: the layout ``inventory_uri`` and
    ``content_file_uri`` place it at."""
    validate_content_id(content_id)
    validate_source_path(path)
    return f"{CONTENTS_SEGMENT}/{content_id}/{FILES_SEGMENT}/{path}"


def content_file_payload_ref(
    content_id: str,
    entry: SourceFileEntry,
    *,
    media_type: str,
    selector: PayloadSelector | None = None,
) -> PayloadRef:
    """The reference to one preserved file of ``content_id`` as its inventory
    ``entry`` pins it (sha256, size), read in place: nothing is copied. The
    inventory records no media type, so it is the builder's assertion from
    source-format evidence, as is a selector (D3)."""
    return PayloadRef(
        uri=format_logical_uri(
            LogicalNamespace.SOURCES, content_file_key(content_id, entry.path)
        ),
        checksum=entry.sha256,
        size_bytes=entry.size_bytes,
        media_type=media_type,
        selector=selector,
    )


def parse_descriptor_uri(uri: str) -> tuple[str, str]:
    """``(sources_root, snapshot_id)`` of a descriptor URI, which must be
    ``{sources_root}/snapshots/{snapshot_id}/descriptor.json`` (D11). Whether
    the snapshot id is the descriptor's own is checked against its bytes by
    whoever reads it."""
    head, snapshot_id = _split_parent(uri, DESCRIPTOR_OBJECT_NAME)
    root, _, segment = head.rpartition("/")
    if segment != SNAPSHOTS_SEGMENT or not root:
        raise SourceLayoutError(f"{uri} is not under a {SNAPSHOTS_SEGMENT}/ prefix")
    try:
        validate_snapshot_id(snapshot_id)
    except ValueError as exc:
        raise SourceLayoutError(f"{uri} does not name a snapshot id") from exc
    return root, snapshot_id


__all__ = [
    "DESCRIPTOR_OBJECT_NAME",
    "INVENTORY_OBJECT_NAME",
    "SourceLayoutError",
    "content_file_key",
    "content_file_payload_ref",
    "content_file_uri",
    "content_files_prefix",
    "content_id_of_inventory_uri",
    "content_root_uri",
    "descriptor_uri",
    "inventory_uri",
    "parse_descriptor_uri",
]
