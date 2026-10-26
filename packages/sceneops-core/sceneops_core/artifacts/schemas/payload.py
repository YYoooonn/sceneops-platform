"""Strict reference to one canonical observation payload (ADR-007 §27.5,
ADR-015 §6.5).

``PayloadRef`` is a closed union discriminated by ``kind``:

    ArtifactPayloadRef  "artifact"     a SceneOps-owned payload object
                                       (recording-derived payloads)
    SourceFileRef       "source_file"  one preserved file of a registered
                                       SourceContent, optionally one frame of it

A canonical manifest never points into an external dataset directory, a
source recording or a storage location. An ``ArtifactPayloadRef`` is
identified by the SceneOps ArtifactRecord that registered its immutable
bytes, and pinned by their checksum and size:

    ArtifactPayloadRef.artifact_id -> ArtifactRecord -> uri -> ArtifactStore

A ``SourceFileRef`` is identified by its content and its path in that
content's inventory, and pinned by the inventory entry's sha256 and size:

    SourceFileRef.(content_id, path) -> inventory entry -> content files -> ArtifactStore

Where the bytes physically live is the ArtifactRecord's (or the inventory
ArtifactRecord's) concern, so moving a storage root or backend never changes
a manifest or its checksum. A reference resolves only to bytes whose
checksum and size equal the reference's; the checksum is what a byte-level
reader verifies against. Consumers that cannot resolve a reference kind
refuse it explicitly; resolution is the one place downstream of canonical
manifests that dispatches on the kind (MS-10).

``media_type`` is an open, lowercase ``type/subtype`` identifier (RFC 6838
shape, no parameters) that must fully specify the payload's encoding and
layout, e.g. ``image/jpeg`` or ``application/x.nuscenes.lidar-pcd-bin``.
Consumers dispatch on it instead of assuming a source's file layout. Like
other open identifiers it is validated, never normalized.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.identifiers import validate_local_id
from sceneops_core.provenance.source_time import SourceTimestampNs
from sceneops_core.sources.content import validate_content_id, validate_source_path

MEDIA_TYPE_PATTERN: Final = (
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)
_MEDIA_TYPE_RE = re.compile(MEDIA_TYPE_PATTERN)


def validate_media_type(value: str) -> str:
    if not isinstance(value, str) or not _MEDIA_TYPE_RE.fullmatch(value):
        raise ValueError(
            f"media_type must be a lowercase 'type/subtype' without parameters, "
            f"got {value!r}"
        )
    return value


class ArtifactPayloadRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["artifact"] = "artifact"
    artifact_id: StrictStr
    checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    size_bytes: StrictInt = Field(ge=1)
    media_type: StrictStr

    @field_validator("artifact_id")
    @classmethod
    def _check_artifact_id(cls, value: str) -> str:
        return validate_local_id(value, field="payload artifact_id")

    @field_validator("media_type")
    @classmethod
    def _check_media_type(cls, value: str) -> str:
        return validate_media_type(value)


class SourceFrame(BaseModel):
    """One frame of a preserved time-based file (a video), selected by its
    presentation time in that file's own time base. It is not a canonical
    timestamp: it names no clock domain and is never compared with one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp_ns: SourceTimestampNs


class SourceFileRef(BaseModel):
    """One preserved file of a registered SourceContent, kept as the source
    holds it: no payload copy is extracted.

    ``sha256`` and ``size_bytes`` are the content inventory entry's for
    ``path``; registration verifies them against the inventory and the
    stored object. ``media_type`` is asserted by the format builder from
    source-format evidence (the inventory records none) and is covered by
    the manifest checksum. ``frame`` selects one frame inside the file;
    absent, the reference is the whole file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["source_file"] = "source_file"
    content_id: StrictStr
    path: StrictStr
    sha256: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    size_bytes: StrictInt = Field(ge=0)
    media_type: StrictStr
    frame: SourceFrame | None = None

    @field_validator("content_id")
    @classmethod
    def _check_content_id(cls, value: str) -> str:
        return validate_content_id(value)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return validate_source_path(value)

    @field_validator("media_type")
    @classmethod
    def _check_media_type(cls, value: str) -> str:
        return validate_media_type(value)


PayloadRef: TypeAlias = Annotated[
    ArtifactPayloadRef | SourceFileRef, Field(discriminator="kind")
]


__all__ = [
    "MEDIA_TYPE_PATTERN",
    "ArtifactPayloadRef",
    "PayloadRef",
    "SourceFileRef",
    "SourceFrame",
    "validate_media_type",
]
