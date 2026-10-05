"""RobotRunManifest v1 -- the durable publication contract for one
finalized robot recording (ADR-007 §8).

A RobotRunManifest holds facts about the source recording and its capture,
nothing a downstream SceneOps process decides: no DatasetVersion, Scene or
Episode references, no processing state, no publication timestamp, no
free-form metadata. Unknown fields are rejected.

The Recording Publisher writes it (as the publication marker, after the
recording itself is durably stored) and ``REGISTER_ROBOT_RUN`` reads it.
Both go through :meth:`RobotRunManifest.to_canonical_bytes` /
:func:`load_canonical_robot_run_manifest`, so there is exactly one
serialization path. Registration rejects manifest bytes that parse but are
not already canonical (I-13).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes

ROBOT_RUN_MANIFEST_SCHEMA_V1: Final = "sceneops.robot_run_manifest/v1"

# run_id / robot_id become object-key path segments and deterministic
# ArtifactRecord ids (≤128 chars, see sceneops_core.common.ids), so they are
# restricted to a path-safe alphabet and run_id leaves room for the id prefix.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RUN_ID_MAX_LENGTH = 100
ROBOT_ID_MAX_LENGTH = 128

_CHECKSUM_PATTERN = r"^sha256:[0-9a-f]{64}$"
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class RobotRunManifestError(ValueError):
    """Manifest bytes are not a valid canonical RobotRunManifest."""


class UnsupportedRobotRunManifestVersionError(RobotRunManifestError):
    pass


class NonCanonicalRobotRunManifestError(RobotRunManifestError):
    """The bytes parse as a valid manifest but are not byte-identical to its
    canonical serialization."""


class RecordingFormat(StrEnum):
    MCAP = "mcap"


class CaptureSourceKind(StrEnum):
    KAFKA = "kafka"
    ROS2_BAG = "ros2_bag"
    FILE = "file"


def validate_identifier(value: str, *, field: str, max_length: int) -> str:
    if len(value) > max_length or not _IDENTIFIER_PATTERN.match(value):
        raise ValueError(
            f"{field} must be 1-{max_length} characters of [A-Za-z0-9._-] "
            f"starting with an alphanumeric character, got {value!r}"
        )
    return value


def normalize_manifest_timestamp(value: datetime) -> datetime:
    """UTC, microsecond precision. Naive datetimes are rejected rather than
    assumed to be UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone-aware, got naive {value!r}")
    return value.astimezone(UTC)


def format_manifest_timestamp(value: datetime) -> str:
    return normalize_manifest_timestamp(value).strftime(_TIMESTAMP_FORMAT)


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecordingRef(_ManifestModel):
    format: RecordingFormat
    uri: StrictStr = Field(min_length=1)
    checksum: StrictStr = Field(pattern=_CHECKSUM_PATTERN)
    size_bytes: StrictInt = Field(ge=1)


class CaptureSource(_ManifestModel):
    kind: CaptureSourceKind
    # Required iff kind == kafka; absent (null) otherwise.
    topics: list[StrictStr] | None = None

    @model_validator(mode="after")
    def _check_topics(self) -> CaptureSource:
        if self.kind == CaptureSourceKind.KAFKA:
            if not self.topics:
                raise ValueError("capture.source.topics is required for kind=kafka")
            if any(not topic for topic in self.topics):
                raise ValueError("capture.source.topics must not contain empty names")
            if self.topics != sorted(set(self.topics)):
                raise ValueError("capture.source.topics must be sorted and unique")
        elif self.topics is not None:
            raise ValueError(
                f"capture.source.topics is only allowed for kind=kafka, "
                f"got kind={self.kind.value}"
            )
        return self


class CaptureInfo(_ManifestModel):
    source: CaptureSource
    source_clock: StrictStr = Field(min_length=1)


class ChannelFact(_ManifestModel):
    topic: StrictStr = Field(min_length=1)
    message_encoding: StrictStr
    schema_name: StrictStr
    schema_encoding: StrictStr
    message_count: StrictInt = Field(ge=1)


class RobotRunManifest(_ManifestModel):
    schema_version: Literal["sceneops.robot_run_manifest/v1"] = (
        ROBOT_RUN_MANIFEST_SCHEMA_V1
    )
    run_id: StrictStr
    robot_id: StrictStr
    # Source assertion only; RobotRecord.platform stays authoritative (§9).
    robot_platform: StrictStr | None = Field(default=None, min_length=1)
    # Min/max message timestamps under capture.source_clock, derived from
    # the recording bytes -- never publication wall-clock time.
    started_at: datetime
    ended_at: datetime
    recording: RecordingRef
    capture: CaptureInfo
    channels: list[ChannelFact] = Field(min_length=1)

    @field_validator("run_id")
    @classmethod
    def _check_run_id(cls, value: str) -> str:
        return validate_identifier(value, field="run_id", max_length=RUN_ID_MAX_LENGTH)

    @field_validator("robot_id")
    @classmethod
    def _check_robot_id(cls, value: str) -> str:
        return validate_identifier(
            value, field="robot_id", max_length=ROBOT_ID_MAX_LENGTH
        )

    @field_validator("started_at", "ended_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return normalize_manifest_timestamp(value)

    @field_serializer("started_at", "ended_at")
    def _serialize_timestamp(self, value: datetime) -> str:
        return format_manifest_timestamp(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> RobotRunManifest:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        topics = [channel.topic for channel in self.channels]
        if topics != sorted(set(topics)):
            raise ValueError("channels must be sorted by topic with unique topics")
        return self

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def load_canonical_robot_run_manifest(data: bytes) -> RobotRunManifest:
    """Strictly parse manifest bytes and require them to be canonical:
    ``canonical(parse(data)) == data`` (ADR-007 §12.1 R2-R3)."""
    try:
        text = data.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RobotRunManifestError(f"manifest is not UTF-8 JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RobotRunManifestError("manifest must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != ROBOT_RUN_MANIFEST_SCHEMA_V1:
        raise UnsupportedRobotRunManifestVersionError(
            f"unsupported RobotRunManifest schema_version: {schema_version!r}"
        )

    try:
        manifest = RobotRunManifest.model_validate(payload)
    except ValidationError as exc:
        raise RobotRunManifestError(f"invalid RobotRunManifest: {exc}") from exc

    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalRobotRunManifestError(
            "manifest bytes are not in canonical RobotRunManifest v1 form"
        )
    return manifest
