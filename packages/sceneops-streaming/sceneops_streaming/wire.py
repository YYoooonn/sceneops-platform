"""Kafka wire mapping for ``TelemetryEnvelope``.

Pure functions only -- no ``confluent_kafka`` import, no network/broker
dependency. This is what keeps "Kafka header encode/decode",
"partition-key derivation", and "consumer reconstruction" unit-testable
without a running broker; ``producer.py``/``consumer.py`` are the thin,
broker-connected layer built on top of this module.

Wire contract (frozen for v1):

    Kafka key      -> robot_run_id (UTF-8 bytes) -- see partition_key()
    Kafka headers  -> every other envelope field, one header per field
    Kafka value    -> payload bytes, exactly as given, never base64
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from sceneops_core.constants.streaming import TELEMETRY_HEADER_PREFIX
from sceneops_core.streaming import TelemetryEnvelope

from .errors import EnvelopeDecodeError

HEADER_VERSION = f"{TELEMETRY_HEADER_PREFIX}version"
HEADER_ROBOT_ID = f"{TELEMETRY_HEADER_PREFIX}robot_id"
HEADER_ROBOT_RUN_ID = f"{TELEMETRY_HEADER_PREFIX}robot_run_id"
HEADER_CHANNEL = f"{TELEMETRY_HEADER_PREFIX}channel"
HEADER_MESSAGE_TYPE = f"{TELEMETRY_HEADER_PREFIX}message_type"
HEADER_SOURCE_TIMESTAMP_NS = f"{TELEMETRY_HEADER_PREFIX}source_timestamp_ns"
HEADER_INGEST_TIMESTAMP_NS = f"{TELEMETRY_HEADER_PREFIX}ingest_timestamp_ns"
HEADER_SEQUENCE_NUMBER = f"{TELEMETRY_HEADER_PREFIX}sequence_number"
HEADER_ENCODING = f"{TELEMETRY_HEADER_PREFIX}encoding"

_REQUIRED_HEADERS = (
    HEADER_VERSION,
    HEADER_ROBOT_ID,
    HEADER_ROBOT_RUN_ID,
    HEADER_CHANNEL,
    HEADER_MESSAGE_TYPE,
    HEADER_SOURCE_TIMESTAMP_NS,
    HEADER_INGEST_TIMESTAMP_NS,
    HEADER_SEQUENCE_NUMBER,
    HEADER_ENCODING,
)


@dataclass(frozen=True)
class EncodedTelemetryRecord:
    """One envelope, mapped onto the Kafka key/headers/value shape a
    producer client actually sends."""

    key: bytes
    headers: list[tuple[str, bytes]]
    value: bytes


def partition_key(envelope: TelemetryEnvelope) -> bytes:
    """Kafka partitioning key for v1 -- ``robot_run_id``, UTF-8 encoded.
    Routes every message for one RobotRun to the same partition
    deterministically; never derived from ``channel`` or any other
    field."""

    return envelope.robot_run_id.encode("utf-8")


def encode_headers(envelope: TelemetryEnvelope) -> list[tuple[str, bytes]]:
    return [
        (HEADER_VERSION, envelope.version.value.encode("utf-8")),
        (HEADER_ROBOT_ID, envelope.robot_id.encode("utf-8")),
        (HEADER_ROBOT_RUN_ID, envelope.robot_run_id.encode("utf-8")),
        (HEADER_CHANNEL, envelope.channel.encode("utf-8")),
        (HEADER_MESSAGE_TYPE, envelope.message_type.encode("utf-8")),
        (
            HEADER_SOURCE_TIMESTAMP_NS,
            str(envelope.source_timestamp_ns).encode("utf-8"),
        ),
        (
            HEADER_INGEST_TIMESTAMP_NS,
            str(envelope.ingest_timestamp_ns).encode("utf-8"),
        ),
        (HEADER_SEQUENCE_NUMBER, str(envelope.sequence_number).encode("utf-8")),
        (HEADER_ENCODING, envelope.encoding.value.encode("utf-8")),
    ]


def encode_envelope(envelope: TelemetryEnvelope) -> EncodedTelemetryRecord:
    """Map a validated envelope onto the Kafka key/headers/value shape.
    ``envelope`` is already a validated ``TelemetryEnvelope`` instance by
    construction (Pydantic) -- this is the producer's own
    validate-required-metadata boundary; there is no second, looser
    validation pass here."""

    return EncodedTelemetryRecord(
        key=partition_key(envelope),
        headers=encode_headers(envelope),
        value=envelope.payload,
    )


def decode_envelope(
    *,
    headers: list[tuple[str, bytes]] | None,
    value: bytes | None,
) -> TelemetryEnvelope:
    """Reconstruct a ``TelemetryEnvelope`` from raw Kafka header/value
    bytes. Raises ``EnvelopeDecodeError`` (never returns ``None``, never
    silently coerces) for any missing header, non-UTF-8 header, unparseable
    integer, or field that fails ``TelemetryEnvelope`` validation --
    including an unknown ``version``."""

    header_map: dict[str, bytes] = {}
    for name, raw in headers or []:
        header_map[name] = raw

    missing = [name for name in _REQUIRED_HEADERS if name not in header_map]
    if missing:
        raise EnvelopeDecodeError(f"missing required header(s): {', '.join(missing)}")

    def _text(name: str) -> str:
        raw = header_map[name]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EnvelopeDecodeError(f"header {name!r} is not valid UTF-8") from exc

    def _int(name: str) -> int:
        text = _text(name)
        try:
            return int(text)
        except ValueError as exc:
            raise EnvelopeDecodeError(
                f"header {name!r} is not a valid integer: {text!r}"
            ) from exc

    if value is None:
        raise EnvelopeDecodeError("record has no value (payload) bytes")

    try:
        return TelemetryEnvelope(
            version=_text(HEADER_VERSION),
            robot_id=_text(HEADER_ROBOT_ID),
            robot_run_id=_text(HEADER_ROBOT_RUN_ID),
            channel=_text(HEADER_CHANNEL),
            message_type=_text(HEADER_MESSAGE_TYPE),
            source_timestamp_ns=_int(HEADER_SOURCE_TIMESTAMP_NS),
            ingest_timestamp_ns=_int(HEADER_INGEST_TIMESTAMP_NS),
            sequence_number=_int(HEADER_SEQUENCE_NUMBER),
            encoding=_text(HEADER_ENCODING),
            payload=value,
        )
    except ValidationError as exc:
        raise EnvelopeDecodeError(f"envelope failed validation: {exc}") from exc
