"""REGISTER_SOURCE_SNAPSHOT: the only way SourceContent and SourceSnapshot
records come into existence (ADR-015 §5).

Input is the URI of a descriptor that the database-free ``publish-snapshot``
(``sceneops_sources.publication``) wrote as its publication marker.
Registration verifies and records; it never writes, copies or rewrites a
preserved object.

    S1  trusted location: the URI is
        {configured source root}/snapshots/{snapshot_id}/descriptor.json;
        the inventory is read from the same root, never from a caller URI
    S2  descriptor: present, strict canonical parse, its own snapshot id
    S3  snapshot registered? same content and descriptor -> no-op (nothing
        else is read); another content or descriptor -> conflict
    S4  inventory: present, strict canonical parse, the descriptor's content;
        the declared selection selects exactly the inventoried paths, and
        each pattern at least one of them
    S5  content registered? another format -> conflict; same format -> its
        files were verified at its first registration and are write-once,
        and the inventory read in S4 has its content_checksum
        not registered -> every stored file is listed and hashed: exactly
        the inventory, byte for byte
    S6  one transaction: inventory ArtifactRecord + content (if absent),
        descriptor ArtifactRecord + snapshot; each insert-if-absent and
        decided again against the stored row (concurrent registrations
        converge or fail); commit

S1-S5 failures write nothing. Retrieval facts stay in the publication's
logs; the Job that registered a record is its ArtifactRecords' ``job_id``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRef,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.ids import (
    source_content_inventory_artifact_id,
    source_snapshot_descriptor_artifact_id,
)
from sceneops_core.sources import (
    InconsistentSourceRegistryError,
    RegistrationOutcome,
    SourceContentError,
    SourceContentInventory,
    SourceContentRecord,
    SourceSelectionError,
    SourceSnapshotDescriptor,
    SourceSnapshotError,
    SourceSnapshotRecord,
    decide_content_registration,
    decide_snapshot_registration,
    load_canonical_source_content_inventory,
    load_canonical_source_snapshot_descriptor,
)
from sceneops_sources import (
    ContentFileError,
    SourceLayoutError,
    inventory_uri,
    parse_descriptor_uri,
    verify_stored_content,
)
from sceneops_storage import ArtifactNotFoundError

from sceneops_worker.core.context import WorkerContext

JSON_MEDIA_TYPE = "application/json"


class SourceSnapshotRegistrationError(RuntimeError):
    """Base class of REGISTER_SOURCE_SNAPSHOT failures that are not registry
    conflicts. None of them leaves a database change behind."""


class UntrustedSourceLocationError(SourceSnapshotRegistrationError):
    """The descriptor URI is not a descriptor key under the configured
    source root."""


class SnapshotNotPublishedError(SourceSnapshotRegistrationError):
    """The descriptor, or the inventory it names, is not stored: the
    publication did not finish."""


class PublishedSnapshotInvalidError(SourceSnapshotRegistrationError):
    """Published descriptor or inventory bytes are not what their keys and
    declaration require."""


class ContentVerificationError(SourceSnapshotRegistrationError):
    """The stored files of a content are not exactly its inventory."""


@dataclass(frozen=True)
class SourceSnapshotRegistration:
    content: SourceContentRecord
    snapshot: SourceSnapshotRecord
    content_outcome: RegistrationOutcome
    snapshot_outcome: RegistrationOutcome


def _trusted_location(context: WorkerContext, descriptor_uri: str) -> tuple[str, str]:
    """S1: ``(source root, snapshot_id)`` of a descriptor URI that names a
    key under the configured root, spelled exactly as the root is."""
    configured_root = context.settings.artifact.source_root_uri
    try:
        root, snapshot_id = parse_descriptor_uri(descriptor_uri)
    except SourceLayoutError as exc:
        raise UntrustedSourceLocationError(str(exc)) from exc
    if root != configured_root:
        raise UntrustedSourceLocationError(
            f"{descriptor_uri} is not under the configured source root "
            f"{configured_root}; only snapshots published there are registered"
        )
    return root, snapshot_id


async def _read_published(context: WorkerContext, uri: str, what: str) -> bytes:
    try:
        return await context.artifact_store.read_bytes(uri)
    except (ArtifactNotFoundError, FileNotFoundError) as exc:
        raise SnapshotNotPublishedError(f"{what} not found: {uri}") from exc


async def _load_descriptor(
    context: WorkerContext, uri: str, snapshot_id: str
) -> tuple[SourceSnapshotDescriptor, bytes]:
    """S2."""
    data = await _read_published(context, uri, "snapshot descriptor")
    try:
        descriptor = load_canonical_source_snapshot_descriptor(data)
    except SourceSnapshotError as exc:
        raise PublishedSnapshotInvalidError(f"{uri}: {exc}") from exc
    if descriptor.snapshot_id != snapshot_id:
        raise PublishedSnapshotInvalidError(
            f"{uri} holds the descriptor of {descriptor.snapshot_id}"
        )
    return descriptor, data


async def _load_inventory(
    context: WorkerContext, uri: str, descriptor: SourceSnapshotDescriptor
) -> tuple[SourceContentInventory, bytes]:
    """S4."""
    data = await _read_published(context, uri, "content inventory")
    try:
        inventory = load_canonical_source_content_inventory(data)
    except SourceContentError as exc:
        raise PublishedSnapshotInvalidError(f"{uri}: {exc}") from exc
    if inventory.content_id != descriptor.content_id:
        raise PublishedSnapshotInvalidError(
            f"{uri} holds the inventory of {inventory.content_id}, the descriptor "
            f"names {descriptor.content_id}"
        )
    paths = tuple(entry.path for entry in inventory.files)
    selection = descriptor.declaration.selection
    try:
        selected = selection.select(paths)
    except SourceSelectionError as exc:
        raise PublishedSnapshotInvalidError(
            f"the selection of {descriptor.snapshot_id} does not match its "
            f"inventory: {exc}"
        ) from exc
    if selected != paths:
        unselected = sorted(set(paths) - set(selected))
        raise PublishedSnapshotInvalidError(
            f"the inventory of {inventory.content_id} holds {len(unselected)} "
            f"file(s) the selection of {descriptor.snapshot_id} does not select, "
            f"e.g. {unselected[:5]}"
        )
    return inventory, data


async def _existing(
    context: WorkerContext, snapshot: SourceSnapshotRecord
) -> SourceSnapshotRegistration:
    content = await context.source_store.get_content(snapshot.content_id)
    if content is None:  # pragma: no cover - excluded by the foreign key
        raise InconsistentSourceRegistryError(
            f"snapshot {snapshot.snapshot_id} names unregistered content "
            f"{snapshot.content_id}"
        )
    return SourceSnapshotRegistration(
        content=content,
        snapshot=snapshot,
        content_outcome=RegistrationOutcome.EXISTING,
        snapshot_outcome=RegistrationOutcome.EXISTING,
    )


async def register_source_snapshot(
    *,
    context: WorkerContext,
    descriptor_uri: str,
    job_id: str | None = None,
) -> SourceSnapshotRegistration:
    # S1, S2
    root, snapshot_id = _trusted_location(context, descriptor_uri)
    descriptor, descriptor_bytes = await _load_descriptor(
        context, descriptor_uri, snapshot_id
    )
    snapshot = SourceSnapshotRecord.for_descriptor(
        descriptor,
        descriptor_artifact_id=source_snapshot_descriptor_artifact_id(snapshot_id),
    )

    # S3
    registered_snapshot = await context.source_store.get_snapshot(snapshot_id)
    if (
        decide_snapshot_registration(snapshot, registered_snapshot)
        == RegistrationOutcome.EXISTING
    ):
        return await _existing(context, registered_snapshot)

    # S4
    target_inventory = inventory_uri(
        context.artifact_store, root, descriptor.content_id
    )
    inventory, inventory_bytes = await _load_inventory(
        context, target_inventory, descriptor
    )
    content = SourceContentRecord.for_inventory(
        inventory,
        format=descriptor.declaration.format,
        inventory_artifact_id=source_content_inventory_artifact_id(
            inventory.content_id
        ),
    )

    # S5
    registered_content = await context.source_store.get_content(inventory.content_id)
    content_known = (
        decide_content_registration(content, registered_content)
        == RegistrationOutcome.EXISTING
    )
    if not content_known:
        try:
            await verify_stored_content(
                context.artifact_store,
                inventory_uri=target_inventory,
                inventory=inventory,
            )
        except ContentFileError as exc:
            raise ContentVerificationError(str(exc)) from exc

    # S6
    try:
        if not content_known:
            await context.artifact_record_store.register(
                artifact_id=content.inventory_artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.SOURCE_CONTENT_INVENTORY,
                    uri=target_inventory,
                    media_type=JSON_MEDIA_TYPE,
                    size_bytes=len(inventory_bytes),
                    checksum=sha256_checksum(inventory_bytes),
                ),
                owner_type=ArtifactOwnerType.SOURCE_CONTENT,
                owner_id=content.content_id,
                job_id=job_id,
            )
        await context.artifact_record_store.register(
            artifact_id=snapshot.descriptor_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.SOURCE_SNAPSHOT_DESCRIPTOR,
                uri=descriptor_uri,
                media_type=JSON_MEDIA_TYPE,
                size_bytes=len(descriptor_bytes),
                checksum=snapshot.descriptor_checksum,
            ),
            owner_type=ArtifactOwnerType.SOURCE_SNAPSHOT,
            owner_id=snapshot_id,
            job_id=job_id,
        )
        registration = await context.source_store.register(
            content=content, snapshot=snapshot
        )
        await context.commit()
    except BaseException:
        await context.rollback()
        raise

    return SourceSnapshotRegistration(
        content=registration.content,
        snapshot=registration.snapshot,
        content_outcome=registration.content_outcome,
        snapshot_outcome=registration.snapshot_outcome,
    )
