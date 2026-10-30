"""Strict references to canonical observation payloads (ADR-007 §27.5,
ADR-015 §6.5, ADR-016, ADR-017).

``PayloadRef`` is the one payload reference of canonical manifests
(SceneManifest v5, EpisodeManifest v4), the same for every source:

    PayloadRef(uri, checksum, size_bytes, media_type, selector)

``uri`` is the Logical URI of immutable stored bytes (ADR-016), and
``checksum`` / ``size_bytes`` pin the whole stored object, also when a
``selector`` picks one part of it. The reference names no artifact, content,
RobotRun, source kind or source path, so a recording-derived payload and a
preserved dataset file are the same kind of value, and moving a storage root
or backend changes neither the manifest nor its checksum. A ``PayloadRef`` is
a ``PinnedPayload``: ``VerifiedPayloadReader`` reads and verifies its bytes
as it is.

Whose bytes a URI may name is a registration fact, not part of this schema:
a recording-built unit references its own RobotRun's observation payloads,
a content-built unit files of its own SourceContent
(``sceneops_worker.payload_registration``).

``media_type`` is validated by ``sceneops_core.artifacts.media_types``.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from sceneops_core.artifacts.logical_uri import LogicalUri, parse_logical_uri
from sceneops_core.artifacts.media_types import (
    media_top_level_type,
    validate_media_type,
)
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.provenance.source_time import SourceTimestampNs


class VideoFrameSelector(BaseModel):
    """One frame of a video payload, selected by its presentation time in the
    video's own time base. It is not a canonical timestamp: it names no clock
    domain and is never compared with one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["video_frame"] = "video_frame"
    timestamp_ns: SourceTimestampNs

    def accepts(self, media_type: str) -> bool:
        return media_top_level_type(media_type) == "video"


# The parts of a payload a reference can select. Each selector carries its
# own ``kind`` so another one can be added without re-reading existing bytes.
PayloadSelector: TypeAlias = VideoFrameSelector


class PayloadRef(BaseModel):
    """Immutable payload bytes by Logical URI, pinned by their full-object
    sha256 and size; ``selector`` absent means the whole object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: StrictStr
    checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    size_bytes: StrictInt = Field(ge=0)
    media_type: StrictStr
    selector: PayloadSelector | None = None

    @field_validator("uri")
    @classmethod
    def _check_uri(cls, value: str) -> str:
        parse_logical_uri(value)
        return value

    @field_validator("media_type")
    @classmethod
    def _check_media_type(cls, value: str) -> str:
        return validate_media_type(value)

    @model_validator(mode="after")
    def _check_selector(self) -> PayloadRef:
        if self.selector is not None and not self.selector.accepts(self.media_type):
            raise ValueError(
                f"a {self.selector.kind} selector cannot select part of a "
                f"{self.media_type} payload"
            )
        return self

    def logical_uri(self) -> LogicalUri:
        return parse_logical_uri(self.uri)


__all__ = [
    "PayloadRef",
    "PayloadSelector",
    "VideoFrameSelector",
]
