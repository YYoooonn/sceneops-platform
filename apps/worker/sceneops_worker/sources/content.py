"""Registered SourceContent as canonical builds and registration read it
(ADR-015 §6.4, §6.5; M3.0 D4, D5).

``resolve_registered_content`` reaches a content's files only through its
registered records: the content row pins ``content_checksum``, its inventory
ArtifactRecord names where the inventory is stored (D4), and the stored
inventory is accepted only with exactly that checksum. No root setting and no
caller URI is involved. The registration gate for references to the content's
files is ``sceneops_worker.payload_registration``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.sources import SourceContentInventory, SourceContentRecord
from sceneops_sources import ContentFileError, load_stored_inventory

from sceneops_worker.core.context import WorkerContext


class RegisteredContentError(RuntimeError):
    """A content is not registered, or its registered records and stored
    inventory disagree."""


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


__all__ = [
    "RegisteredContent",
    "RegisteredContentError",
    "resolve_registered_content",
]
