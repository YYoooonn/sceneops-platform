"""Registered SourceContent as canonical builds and registration read it
(ADR-015 §6.4, §6.5; M3.0 D3, D4, D5).

``resolve_registered_content`` reaches a content's files only through its
registered records: the content row pins ``content_checksum``, its inventory
ArtifactRecord names where the inventory is stored (D4), and the stored
inventory is accepted only with exactly that checksum. No root setting and no
caller URI is involved.

``verify_source_file_refs`` is the registration gate for preserved-file
payload references (R7, MS-7): every ``SourceFileRef`` names a file of a
registered content with exactly the inventory entry's sha256 and size, and
that file's object is present in the store with its inventory size (one
listing per content prefix). The content's files were fully hashed when it
was first registered and are write-once, so presence and size are checked
here, not the bytes again. The media type is the builder's assertion, covered
by the manifest checksum (D3).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sceneops_core.artifacts.schemas import ArtifactKind, SourceFileRef
from sceneops_core.sources import (
    SourceContentInventory,
    SourceContentRecord,
    SourceFileEntry,
)
from sceneops_sources import (
    ContentFileError,
    content_file_uri,
    content_files_prefix,
    load_stored_inventory,
)

from sceneops_worker.core.context import WorkerContext


class RegisteredContentError(RuntimeError):
    """A content is not registered, or its registered records and stored
    inventory disagree."""


class SourceFileRefError(RuntimeError):
    """A preserved-file reference does not match its content's inventory or
    stored files."""


@dataclass(frozen=True)
class RegisteredContent:
    record: SourceContentRecord
    inventory_uri: str
    inventory: SourceContentInventory


async def resolve_registered_content(
    context: WorkerContext, content_id: str
) -> RegisteredContent:
    record = await context.source_store.get_content(content_id)
    if record is None:
        raise RegisteredContentError(f"SourceContent is not registered: {content_id}")
    artifact = await context.artifact_record_store.get(record.inventory_artifact_id)
    if artifact is None or artifact.kind != ArtifactKind.SOURCE_CONTENT_INVENTORY:
        raise RegisteredContentError(
            f"SourceContent {content_id} names inventory artifact "
            f"{record.inventory_artifact_id}, which is missing or not an inventory"
        )
    try:
        inventory = await load_stored_inventory(
            context.artifact_store,
            artifact.uri,
            content_checksum=record.content_checksum,
        )
    except ContentFileError as exc:
        raise RegisteredContentError(f"SourceContent {content_id}: {exc}") from exc
    return RegisteredContent(
        record=record, inventory_uri=artifact.uri, inventory=inventory
    )


def _mismatch(
    ref: SourceFileRef, content_id: str, entries: dict[str, SourceFileEntry]
) -> str | None:
    if ref.content_id != content_id:
        return f"{ref.path!r} is referenced in {ref.content_id}, not {content_id}"
    entry = entries.get(ref.path)
    if entry is None:
        return f"{ref.path!r} is not a file of {ref.content_id}"
    if (entry.sha256, entry.size_bytes) != (ref.sha256, ref.size_bytes):
        return (
            f"{ref.path!r} is referenced as {ref.size_bytes} bytes {ref.sha256}; the "
            f"inventory pins {entry.size_bytes} bytes {entry.sha256}"
        )
    return None


async def verify_source_file_refs(
    context: WorkerContext,
    content: RegisteredContent,
    refs: Iterable[SourceFileRef],
) -> None:
    entries = {entry.path: entry for entry in content.inventory.files}
    referenced = {
        (ref.path, ref.sha256, ref.size_bytes, ref.content_id): ref for ref in refs
    }
    problems = sorted(
        {
            problem
            for ref in referenced.values()
            if (problem := _mismatch(ref, content.record.content_id, entries))
            is not None
        }
    )
    if problems:
        raise SourceFileRefError(
            f"{len(problems)} source file reference(s) do not match the inventory: "
            f"{problems[:5]}"
        )
    if not referenced:
        return
    stored = {
        obj.uri: obj.size_bytes
        for obj in await context.artifact_store.list_objects(
            content_files_prefix(content.inventory_uri)
        )
    }
    absent = sorted(
        {
            ref.path
            for ref in referenced.values()
            if stored.get(content_file_uri(content.inventory_uri, ref.path))
            != ref.size_bytes
        }
    )
    if absent:
        raise SourceFileRefError(
            f"{len(absent)} referenced file(s) of {content.record.content_id} are not "
            f"stored with their inventory size: {absent[:5]}"
        )


__all__ = [
    "RegisteredContent",
    "RegisteredContentError",
    "SourceFileRefError",
    "resolve_registered_content",
    "verify_source_file_refs",
]
