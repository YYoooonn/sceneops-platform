"""Strict reference to one canonical observation payload (ADR-007 §27.5).

A canonical manifest never points into an external dataset directory, a
source recording or a storage location. A payload is identified by the
SceneOps ArtifactRecord that registered its immutable bytes, and pinned by
their checksum and size:

    PayloadRef.artifact_id -> ArtifactRecord -> uri -> ArtifactStore

Where the bytes physically live is the ArtifactRecord's concern, so moving
a storage root or backend never changes a manifest or its checksum. A
reference resolves only to an ArtifactRecord whose checksum, size and media
type equal the reference's; the checksum is what a byte-level reader
verifies against.

``media_type`` is an open, lowercase ``type/subtype`` identifier (RFC 6838
shape, no parameters) that must fully specify the payload's encoding and
layout, e.g. ``image/jpeg`` or ``application/x.nuscenes.lidar-pcd-bin``.
Consumers dispatch on it instead of assuming a source's file layout. Like
other open identifiers it is validated, never normalized.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.identifiers import validate_local_id

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


class PayloadRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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


__all__ = ["MEDIA_TYPE_PATTERN", "PayloadRef", "validate_media_type"]
