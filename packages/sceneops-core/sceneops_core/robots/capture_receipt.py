"""CaptureReceipt v1 -- immutable acquisition metadata written by Capture
next to a finalized recording (ADR-008 §4.2 A1).

The receipt holds what a finalized MCAP cannot say about itself and what the
Publisher therefore cannot re-derive: which robot produced the run, which
Kafka topics it was captured from, the source clock, and why the capture
ended. It is *publication input and diagnostics only*:

* it is not canonical Scene/Episode metadata and not canonical provenance;
  it is never uploaded as provenance and never read by registration;
* it never overrides or manufactures a fact that the MCAP bytes carry. The
  recording checksum, size, message count and per-channel counts recorded
  here are *claims about the bytes*: the Publisher re-derives all of them
  from the bytes and fails on any disagreement;
* it is written once, inside the capture bag directory before the atomic
  rename that finalizes the bag, and never modified afterwards.

Like :mod:`sceneops_core.robots.manifest`, the receipt has exactly one
serialization (canonical JSON) and the loader rejects bytes that parse but
are not already canonical, so a hand-edited receipt fails loudly.
"""

from __future__ import annotations

import json
from datetime import datetime
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
from sceneops_core.robots.manifest import (
    ROBOT_ID_MAX_LENGTH,
    RUN_ID_MAX_LENGTH,
    CaptureInfo,
    CaptureSourceKind,
    RecordingFormat,
    format_manifest_timestamp,
    normalize_manifest_timestamp,
    validate_identifier,
)

CAPTURE_RECEIPT_SCHEMA_V1: Final = "sceneops.capture_receipt/v1"
CAPTURE_RECEIPT_FILENAME: Final = "capture_receipt.json"

_CHECKSUM_PATTERN = r"^sha256:[0-9a-f]{64}$"
# A file name inside the bag directory: no path separators, no traversal.
_BAG_FILE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*\.mcap$"


class CaptureReceiptError(ValueError):
    """Receipt bytes are not a valid canonical CaptureReceipt."""


class UnsupportedCaptureReceiptVersionError(CaptureReceiptError):
    pass


class NonCanonicalCaptureReceiptError(CaptureReceiptError):
    """The bytes parse as a valid receipt but are not byte-identical to its
    canonical serialization."""


class FinalizationReason(StrEnum):
    EXPLICIT_RUN_END = "explicit_run_end"
    IDLE_TIMEOUT = "idle_timeout"
    MAX_MESSAGES = "max_messages"
    MANUAL = "manual"


class _ReceiptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReceiptRecording(_ReceiptModel):
    file: StrictStr = Field(pattern=_BAG_FILE_PATTERN)
    format: RecordingFormat
    checksum: StrictStr = Field(pattern=_CHECKSUM_PATTERN)
    size_bytes: StrictInt = Field(ge=1)


class ReceiptFinalization(_ReceiptModel):
    reason: FinalizationReason
    # Capture-host wall clock; indicative only across hosts.
    finalized_at: datetime

    @field_validator("finalized_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return normalize_manifest_timestamp(value)

    @field_serializer("finalized_at")
    def _serialize_timestamp(self, value: datetime) -> str:
        return format_manifest_timestamp(value)


class ReceiptKafka(_ReceiptModel):
    partition: StrictInt = Field(ge=0)
    first_offset: StrictInt = Field(ge=0)
    last_offset: StrictInt = Field(ge=0)
    # Telemetry sequence numbers (control events have their own space).
    first_sequence: StrictInt = Field(ge=0)
    last_sequence: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def _check_ranges(self) -> ReceiptKafka:
        if self.last_offset < self.first_offset:
            raise ValueError("kafka.last_offset must be >= kafka.first_offset")
        if self.last_sequence < self.first_sequence:
            raise ValueError("kafka.last_sequence must be >= kafka.first_sequence")
        return self


class CaptureReceipt(_ReceiptModel):
    schema_version: Literal["sceneops.capture_receipt/v1"] = CAPTURE_RECEIPT_SCHEMA_V1
    run_id: StrictStr
    robot_id: StrictStr
    # Source assertion as supplied to capture; null when none was supplied.
    robot_platform: StrictStr | None = Field(default=None, min_length=1)
    recording: ReceiptRecording
    # The same value type the manifest carries, so a receipt can only hold
    # inputs the manifest accepts.
    capture: CaptureInfo
    message_count: StrictInt = Field(ge=1)
    per_channel_counts: dict[StrictStr, StrictInt] = Field(min_length=1)
    finalization: ReceiptFinalization
    kafka: ReceiptKafka

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

    @field_validator("per_channel_counts")
    @classmethod
    def _check_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not topic or count < 1 for topic, count in value.items()):
            raise ValueError(
                "per_channel_counts must map non-empty topics to counts >= 1"
            )
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> CaptureReceipt:
        if self.capture.source.kind != CaptureSourceKind.KAFKA:
            raise ValueError("a capture receipt describes a Kafka capture")
        if sum(self.per_channel_counts.values()) != self.message_count:
            raise ValueError("per_channel_counts must sum to message_count")
        return self

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


def load_canonical_capture_receipt(data: bytes) -> CaptureReceipt:
    """Strictly parse receipt bytes and require them to be canonical:
    ``canonical(parse(data)) == data``."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureReceiptError(f"receipt is not UTF-8 JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise CaptureReceiptError("receipt must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != CAPTURE_RECEIPT_SCHEMA_V1:
        raise UnsupportedCaptureReceiptVersionError(
            f"unsupported CaptureReceipt schema_version: {schema_version!r}"
        )

    try:
        receipt = CaptureReceipt.model_validate(payload)
    except ValidationError as exc:
        raise CaptureReceiptError(f"invalid CaptureReceipt: {exc}") from exc

    if receipt.to_canonical_bytes() != data:
        raise NonCanonicalCaptureReceiptError(
            "receipt bytes are not in canonical CaptureReceipt v1 form"
        )
    return receipt
