"""SourceSnapshot: one upstream declaration of where a SourceContent came from
(ADR-015 §3, §5).

::

    declaration         identity-defining
      format            open identifier (nuscenes, lerobot)
      upstream          the source's own dataset identifier, verbatim
      upstream_revision the source's content revision (an HF commit) or, where
                        it has none, its release name (v1.0-mini), verbatim
      selection         the include patterns that chose the files from the
                        source tree; selection_checksum = sha256 of its
                        canonical form
    snapshot_id         snapshot-<sha256(canonical_json({snapshot_id_schema, format,
                        upstream, upstream_revision, selection_checksum}))[:32]>

    descriptor          sceneops.source_snapshot_descriptor/v1 (canonical JSON,
                        write-once)
      declaration
      content_id        the bytes this declaration names
      annotations       licence and other source-declared notes: provenance,
                        never identity

``snapshot_id`` depends on the declaration only, never on the bytes or the
annotations, so a declaration fetched twice with different bytes, or
described twice with different annotations, is one id with a conflict that
registration detects. The format's own version is not declared: a format
reader derives it from the bytes. Retrieval facts (local path, URL, time,
operator) are execution history and have no field here.
"""

from __future__ import annotations

import hashlib
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationInfo,
    field_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.identifiers import (
    validate_external_format,
    validate_open_identifier,
    validate_verbatim_key,
)

from ._canonical import load_canonical_model
from .content import SOURCE_PATH_MAX_LENGTH, validate_content_id

SOURCE_SNAPSHOT_DESCRIPTOR_SCHEMA_V1: Final = "sceneops.source_snapshot_descriptor/v1"
SNAPSHOT_ID_SCHEMA_V1: Final = "sceneops.snapshot_id/v1"
SNAPSHOT_ID_PREFIX: Final = "snapshot-"
_ID_DIGEST_HEX_LENGTH: Final = 32


class SourceSnapshotError(ValueError):
    """Bytes are not a valid canonical SourceSnapshot descriptor."""


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotSelection(_SnapshotModel):
    """Which files of the source tree were preserved, so what was *not*
    preserved is explicit. Patterns are relative to the source root and kept
    verbatim; their matching is applied by publication, which records the
    result as the content inventory."""

    include: tuple[StrictStr, ...] = Field(min_length=1)

    @field_validator("include")
    @classmethod
    def _check_include(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            validate_verbatim_key(
                pattern, field="include pattern", max_length=SOURCE_PATH_MAX_LENGTH
            )
            if pattern.startswith("/"):
                raise ValueError(
                    f"include pattern must be relative to the source root, got "
                    f"{pattern!r}"
                )
        if list(value) != sorted(set(value)):
            raise ValueError("include patterns must be sorted and unique")
        return value

    @property
    def selection_checksum(self) -> str:
        return sha256_checksum(canonical_json_bytes(self.model_dump(mode="json")))


class SnapshotDeclaration(_SnapshotModel):
    format: StrictStr
    upstream: StrictStr
    upstream_revision: StrictStr
    selection: SnapshotSelection

    @field_validator("format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        return validate_external_format(value)

    @field_validator("upstream", "upstream_revision")
    @classmethod
    def _check_verbatim(cls, value: str, info: ValidationInfo) -> str:
        return validate_verbatim_key(value, field=info.field_name)

    @property
    def snapshot_id(self) -> str:
        return snapshot_id_for(self)


def snapshot_id_for(declaration: SnapshotDeclaration) -> str:
    document = {
        "snapshot_id_schema": SNAPSHOT_ID_SCHEMA_V1,
        "format": declaration.format,
        "upstream": declaration.upstream,
        "upstream_revision": declaration.upstream_revision,
        "selection_checksum": declaration.selection.selection_checksum,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{SNAPSHOT_ID_PREFIX}{digest[:_ID_DIGEST_HEX_LENGTH]}"


class SourceSnapshotDescriptor(_SnapshotModel):
    """The published record of one declaration, bound once to one content."""

    schema_version: Literal["sceneops.source_snapshot_descriptor/v1"] = (
        SOURCE_SNAPSHOT_DESCRIPTOR_SCHEMA_V1
    )
    declaration: SnapshotDeclaration
    content_id: StrictStr
    annotations: dict[StrictStr, StrictStr] = Field(default_factory=dict)

    @field_validator("content_id")
    @classmethod
    def _check_content_id(cls, value: str) -> str:
        return validate_content_id(value)

    @field_validator("annotations")
    @classmethod
    def _check_annotations(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            validate_open_identifier(key, field="annotation key")
        return value

    @property
    def snapshot_id(self) -> str:
        return self.declaration.snapshot_id

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @property
    def descriptor_checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())


def load_canonical_source_snapshot_descriptor(data: bytes) -> SourceSnapshotDescriptor:
    """Strictly parse descriptor bytes and require them to be canonical."""
    return load_canonical_model(
        data,
        SourceSnapshotDescriptor,
        schema_version=SOURCE_SNAPSHOT_DESCRIPTOR_SCHEMA_V1,
        error=SourceSnapshotError,
    )


__all__ = [
    "SNAPSHOT_ID_PREFIX",
    "SNAPSHOT_ID_SCHEMA_V1",
    "SOURCE_SNAPSHOT_DESCRIPTOR_SCHEMA_V1",
    "SnapshotDeclaration",
    "SnapshotSelection",
    "SourceSnapshotDescriptor",
    "SourceSnapshotError",
    "load_canonical_source_snapshot_descriptor",
    "snapshot_id_for",
]
