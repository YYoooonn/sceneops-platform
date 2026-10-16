"""SourceContent: the identity of one set of preserved native bytes
(ADR-015 §3, §5).

The inventory is the identity-defining record of a SourceContent::

    inventory          sceneops.source_content_inventory/v1 (canonical JSON, write-once)
      files[]          path (relative to the source root, verbatim), size_bytes, sha256;
                       sorted by path, unique
      content_checksum sha256 of canonical_json(files[])
    content_id         content-<sha256(canonical_json({content_id_schema,
                                                       content_checksum}))[:32]>

``content_id`` depends on the file list only. Format, upstream dataset and
revision, selection rules, annotations, storage location and time are not
inputs, so identical bytes declared by several SourceSnapshots are one
content. The inventory interprets nothing: it decides no unit, clock or
channel meaning.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN, sha256_checksum
from sceneops_core.common.identifiers import validate_verbatim_key

from ._canonical import load_canonical_model

SOURCE_CONTENT_INVENTORY_SCHEMA_V1: Final = "sceneops.source_content_inventory/v1"
CONTENT_ID_SCHEMA_V1: Final = "sceneops.content_id/v1"
CONTENT_ID_PREFIX: Final = "content-"
CONTENT_ID_PATTERN: Final = rf"^{CONTENT_ID_PREFIX}[0-9a-f]{{32}}$"
SOURCE_PATH_MAX_LENGTH: Final = 1024
_ID_DIGEST_HEX_LENGTH: Final = 32
_CONTENT_ID_RE: Final = re.compile(CONTENT_ID_PATTERN)


class SourceContentError(ValueError):
    """Bytes are not a valid canonical SourceContent inventory."""


def validate_source_path(value: str) -> str:
    """A file path relative to the source root, kept verbatim.

    Paths are never normalized, so one that has more than one spelling of
    the same file (absolute, ``.`` / ``..`` or empty segments, a trailing
    ``/``, a backslash separator) is rejected rather than rewritten.
    """
    validate_verbatim_key(value, field="path", max_length=SOURCE_PATH_MAX_LENGTH)
    if "\\" in value:
        raise ValueError(f"path must use '/' separators, got {value!r}")
    if value.startswith("/"):
        raise ValueError(f"path must be relative to the source root, got {value!r}")
    if any(segment in ("", ".", "..") for segment in value.split("/")):
        raise ValueError(
            f"path must not contain empty, '.' or '..' segments, got {value!r}"
        )
    return value


class _ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceFileEntry(_ContentModel):
    path: StrictStr
    size_bytes: StrictInt = Field(ge=0)
    sha256: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return validate_source_path(value)


def content_checksum_for(files: Iterable[SourceFileEntry]) -> str:
    """sha256 of the canonical file list. ``files`` must already be in
    inventory order (sorted by path)."""
    return sha256_checksum(
        canonical_json_bytes([entry.model_dump(mode="json") for entry in files])
    )


def content_id_for(content_checksum: str) -> str:
    document = {
        "content_id_schema": CONTENT_ID_SCHEMA_V1,
        "content_checksum": content_checksum,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{CONTENT_ID_PREFIX}{digest[:_ID_DIGEST_HEX_LENGTH]}"


def validate_content_id(value: str) -> str:
    if not isinstance(value, str) or not _CONTENT_ID_RE.fullmatch(value):
        raise ValueError(f"content_id must match {CONTENT_ID_PATTERN}, got {value!r}")
    return value


class SourceContentInventory(_ContentModel):
    """The checksummed registry of every preserved file of one content."""

    schema_version: Literal["sceneops.source_content_inventory/v1"] = (
        SOURCE_CONTENT_INVENTORY_SCHEMA_V1
    )
    files: tuple[SourceFileEntry, ...] = Field(min_length=1)
    content_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    @model_validator(mode="after")
    def _check_invariants(self) -> SourceContentInventory:
        paths = [entry.path for entry in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("files must be sorted by path with unique paths")
        expected = content_checksum_for(self.files)
        if self.content_checksum != expected:
            raise ValueError(
                f"content_checksum must be {expected} for these files, got "
                f"{self.content_checksum}"
            )
        return self

    @classmethod
    def create(cls, files: Iterable[SourceFileEntry]) -> SourceContentInventory:
        ordered = tuple(sorted(files, key=lambda entry: entry.path))
        return cls(files=ordered, content_checksum=content_checksum_for(ordered))

    @property
    def content_id(self) -> str:
        return content_id_for(self.content_checksum)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.files)

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def load_canonical_source_content_inventory(data: bytes) -> SourceContentInventory:
    """Strictly parse inventory bytes and require them to be canonical."""
    return load_canonical_model(
        data,
        SourceContentInventory,
        schema_version=SOURCE_CONTENT_INVENTORY_SCHEMA_V1,
        error=SourceContentError,
    )


__all__ = [
    "CONTENT_ID_PATTERN",
    "CONTENT_ID_PREFIX",
    "CONTENT_ID_SCHEMA_V1",
    "SOURCE_CONTENT_INVENTORY_SCHEMA_V1",
    "SOURCE_PATH_MAX_LENGTH",
    "SourceContentError",
    "SourceContentInventory",
    "SourceFileEntry",
    "content_checksum_for",
    "content_id_for",
    "load_canonical_source_content_inventory",
    "validate_content_id",
    "validate_source_path",
]
