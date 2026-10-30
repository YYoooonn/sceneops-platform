"""The Source Registry: immutable records of registered source preservation
(ADR-015 §3, §5; MS-3, MS-6a).

::

    SourceContentRecord    one set of preserved bytes; its inventory
                           ArtifactRecord pins the inventory bytes, and
                           ``format`` is fixed by the first registration
    SourceSnapshotRecord   one upstream declaration of those bytes; its
                           descriptor ArtifactRecord pins the descriptor bytes

Both are inserted once, together, by ``REGISTER_SOURCE_SNAPSHOT`` and never
updated: neither has a status. Registration decides each record against the
one already registered under its id (``decide_content_registration``,
``decide_snapshot_registration``)::

    content    absent                                   CREATED
               present, same format                     EXISTING (reused)
               present, another format                  ContentFormatConflictError
    snapshot   absent                                   CREATED
               present, same content and descriptor     EXISTING (no-op)
               present, another content_id              SnapshotContentConflictError
               present, another descriptor              SnapshotDescriptorConflictError

Every other field of a record is a function of its id's inputs (the
inventory for a content, the declaration for a snapshot), so a registered
record that disagrees with the candidate on one of them means the registry
itself is inconsistent; that is reported and never decided on.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, StrictInt, StrictStr, field_validator

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.identifiers import validate_external_format
from sceneops_core.common.schemas import SceneOpsBaseModel

from .content import SourceContentInventory, validate_content_id
from .snapshot import SourceSnapshotDescriptor, validate_snapshot_id


class SourceRegistrationError(ValueError):
    """A registration contradicts the Source Registry. Nothing is written."""


class ContentFormatConflictError(SourceRegistrationError):
    """The same bytes are declared under another format than the one their
    first registration fixed."""


class SnapshotContentConflictError(SourceRegistrationError):
    """One declaration names two byte sets: the declared revision was fetched
    twice with different bytes."""


class SnapshotDescriptorConflictError(SourceRegistrationError):
    """One declaration is described twice with different descriptor bytes
    (other annotations)."""


class InconsistentSourceRegistryError(SourceRegistrationError):
    """A registered record disagrees with fields its own id determines."""


class RegistrationOutcome(StrEnum):
    CREATED = "created"
    EXISTING = "existing"


class SourceContentRecord(SceneOpsBaseModel):
    content_id: StrictStr
    format: StrictStr
    content_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    inventory_artifact_id: StrictStr
    file_count: StrictInt = Field(gt=0)
    total_bytes: StrictInt = Field(ge=0)
    # DB insert time; not part of the record's identity.
    registered_at: datetime | None = None

    @field_validator("content_id")
    @classmethod
    def _check_content_id(cls, value: str) -> str:
        return validate_content_id(value)

    @field_validator("format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        return validate_external_format(value)

    @classmethod
    def for_inventory(
        cls,
        inventory: SourceContentInventory,
        *,
        format: str,
        inventory_artifact_id: str,
    ) -> SourceContentRecord:
        return cls(
            content_id=inventory.content_id,
            format=format,
            content_checksum=inventory.content_checksum,
            inventory_artifact_id=inventory_artifact_id,
            file_count=len(inventory.files),
            total_bytes=inventory.total_bytes,
        )


class SourceSnapshotRecord(SceneOpsBaseModel):
    snapshot_id: StrictStr
    content_id: StrictStr
    format: StrictStr
    upstream: StrictStr
    upstream_revision: StrictStr
    selection_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    descriptor_artifact_id: StrictStr
    descriptor_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    registered_at: datetime | None = None

    @field_validator("snapshot_id")
    @classmethod
    def _check_snapshot_id(cls, value: str) -> str:
        return validate_snapshot_id(value)

    @field_validator("content_id")
    @classmethod
    def _check_content_id(cls, value: str) -> str:
        return validate_content_id(value)

    @field_validator("format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        return validate_external_format(value)

    @classmethod
    def for_descriptor(
        cls,
        descriptor: SourceSnapshotDescriptor,
        *,
        descriptor_artifact_id: str,
    ) -> SourceSnapshotRecord:
        declaration = descriptor.declaration
        return cls(
            snapshot_id=descriptor.snapshot_id,
            content_id=descriptor.content_id,
            format=declaration.format,
            upstream=declaration.upstream,
            upstream_revision=declaration.upstream_revision,
            selection_checksum=declaration.selection.selection_checksum,
            descriptor_artifact_id=descriptor_artifact_id,
            descriptor_checksum=descriptor.descriptor_checksum,
        )


def _identity_fields(record: SceneOpsBaseModel) -> dict[str, object]:
    return record.model_dump(exclude={"registered_at"})


def _require_consistent(
    candidate: SceneOpsBaseModel, registered: SceneOpsBaseModel, what: str
) -> None:
    expected, stored = _identity_fields(candidate), _identity_fields(registered)
    differing = sorted(k for k in expected if expected[k] != stored[k])
    if differing:
        raise InconsistentSourceRegistryError(
            f"{what} is registered with {differing} other than its id determines: "
            f"registered {[stored[k] for k in differing]}, "
            f"expected {[expected[k] for k in differing]}"
        )


def decide_content_registration(
    candidate: SourceContentRecord, registered: SourceContentRecord | None
) -> RegistrationOutcome:
    if registered is None:
        return RegistrationOutcome.CREATED
    if registered.content_id != candidate.content_id:
        raise ValueError("registered and candidate name different contents")
    if registered.format != candidate.format:
        raise ContentFormatConflictError(
            f"content {candidate.content_id} is registered as format "
            f"{registered.format!r}; the same bytes cannot be declared as "
            f"{candidate.format!r}"
        )
    _require_consistent(candidate, registered, f"content {candidate.content_id}")
    return RegistrationOutcome.EXISTING


def decide_snapshot_registration(
    candidate: SourceSnapshotRecord, registered: SourceSnapshotRecord | None
) -> RegistrationOutcome:
    if registered is None:
        return RegistrationOutcome.CREATED
    if registered.snapshot_id != candidate.snapshot_id:
        raise ValueError("registered and candidate name different snapshots")
    if registered.content_id != candidate.content_id:
        raise SnapshotContentConflictError(
            f"snapshot {candidate.snapshot_id} is registered for content "
            f"{registered.content_id}; one declared revision cannot name "
            f"{candidate.content_id} too"
        )
    if registered.descriptor_checksum != candidate.descriptor_checksum:
        raise SnapshotDescriptorConflictError(
            f"snapshot {candidate.snapshot_id} is registered with descriptor "
            f"{registered.descriptor_checksum}; a declaration has one descriptor, "
            f"not also {candidate.descriptor_checksum}"
        )
    _require_consistent(candidate, registered, f"snapshot {candidate.snapshot_id}")
    return RegistrationOutcome.EXISTING


__all__ = [
    "ContentFormatConflictError",
    "InconsistentSourceRegistryError",
    "RegistrationOutcome",
    "SnapshotContentConflictError",
    "SnapshotDescriptorConflictError",
    "SourceContentRecord",
    "SourceRegistrationError",
    "SourceSnapshotRecord",
    "decide_content_registration",
    "decide_snapshot_registration",
]
