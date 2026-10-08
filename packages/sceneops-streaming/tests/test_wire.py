"""Unit tests for the Kafka wire mapping.

Pure functions only -- no ``confluent_kafka`` client is constructed, no
broker connection is attempted. Kafka-backed behavior (real produce/
consume against a live broker) is exercised by `make smoke-streaming`,
not here.
"""

from __future__ import annotations

import pytest

from sceneops_streaming import EnvelopeEncoding, TelemetryEnvelope

from sceneops_streaming.errors import EnvelopeDecodeError
from sceneops_streaming.wire import (
    HEADER_CHANNEL,
    HEADER_ENCODING,
    HEADER_INGEST_TIMESTAMP_NS,
    HEADER_MESSAGE_TYPE,
    HEADER_ROBOT_ID,
    HEADER_ROBOT_RUN_ID,
    HEADER_SEQUENCE_NUMBER,
    HEADER_SOURCE_TIMESTAMP_NS,
    HEADER_VERSION,
    decode_envelope,
    encode_envelope,
    partition_key,
)


def _envelope(**overrides: object) -> TelemetryEnvelope:
    kwargs = dict(
        robot_id="robot-1",
        robot_run_id="robotrun-1",
        channel="/vehicle/imu",
        message_type="sensor_msgs/msg/Imu",
        source_timestamp_ns=1_700_000_000_000_000_000,
        ingest_timestamp_ns=1_700_000_000_250_000_000,
        sequence_number=7,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00]),
    )
    kwargs.update(overrides)
    return TelemetryEnvelope(**kwargs)


def test_partition_key_is_robot_run_id_utf8() -> None:
    envelope = _envelope(robot_run_id="robotrun-42")
    assert partition_key(envelope) == b"robotrun-42"


def test_encode_then_decode_round_trips_exactly() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)

    assert record.key == b"robotrun-1"
    assert record.value == envelope.payload

    decoded = decode_envelope(headers=record.headers, value=record.value)
    assert decoded == envelope


def test_encode_decode_round_trip_preserves_non_utf8_binary_payload() -> None:
    payload = bytes([0xFF, 0xFE, 0x00, 0x80, 0x81])
    envelope = _envelope(payload=payload)
    record = encode_envelope(envelope)

    decoded = decode_envelope(headers=record.headers, value=record.value)
    assert decoded.payload == payload


def test_encode_headers_use_expected_kafka_header_names() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    header_names = {name for name, _ in record.headers}

    assert header_names == {
        HEADER_VERSION,
        HEADER_ROBOT_ID,
        HEADER_ROBOT_RUN_ID,
        HEADER_CHANNEL,
        HEADER_MESSAGE_TYPE,
        HEADER_SOURCE_TIMESTAMP_NS,
        HEADER_INGEST_TIMESTAMP_NS,
        HEADER_SEQUENCE_NUMBER,
        HEADER_ENCODING,
    }


def test_decoded_envelope_preserves_source_and_ingest_timestamps_independently() -> (
    None
):
    """source_timestamp_ns and ingest_timestamp_ns are semantically
    distinct clocks -- each must round-trip to its own exact value. They
    are NOT required to differ numerically (see
    test_encode_decode_round_trip_preserves_equal_source_and_ingest_timestamps
    below); this test intentionally uses different values only to prove
    neither one clobbers the other, not to assert inequality is required."""

    envelope = _envelope(
        source_timestamp_ns=1_000_000_000,
        ingest_timestamp_ns=2_000_000_000,
    )
    record = encode_envelope(envelope)
    decoded = decode_envelope(headers=record.headers, value=record.value)

    assert decoded.source_timestamp_ns == 1_000_000_000
    assert decoded.ingest_timestamp_ns == 2_000_000_000


def test_encode_decode_round_trip_preserves_equal_source_and_ingest_timestamps() -> (
    None
):
    """source_timestamp_ns == ingest_timestamp_ns is legal -- they are
    semantically distinct concepts, not values required to differ. The
    wire mapping must preserve equal values exactly, not coerce or reject
    them."""

    envelope = _envelope(source_timestamp_ns=42, ingest_timestamp_ns=42)
    record = encode_envelope(envelope)
    decoded = decode_envelope(headers=record.headers, value=record.value)

    assert decoded.source_timestamp_ns == 42
    assert decoded.ingest_timestamp_ns == 42
    assert decoded == envelope


def test_decode_missing_headers_raises_envelope_decode_error() -> None:
    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=None, value=b"payload")


def test_decode_missing_robot_run_id_header_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (name, value) for name, value in record.headers if name != HEADER_ROBOT_RUN_ID
    ]

    with pytest.raises(EnvelopeDecodeError, match=HEADER_ROBOT_RUN_ID):
        decode_envelope(headers=headers, value=record.value)


def test_decode_missing_channel_header_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (name, value) for name, value in record.headers if name != HEADER_CHANNEL
    ]

    with pytest.raises(EnvelopeDecodeError, match=HEADER_CHANNEL):
        decode_envelope(headers=headers, value=record.value)


def test_decode_missing_encoding_header_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (name, value) for name, value in record.headers if name != HEADER_ENCODING
    ]

    with pytest.raises(EnvelopeDecodeError, match=HEADER_ENCODING):
        decode_envelope(headers=headers, value=record.value)


def test_decode_invalid_timestamp_header_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (HEADER_SOURCE_TIMESTAMP_NS, b"not-a-number")
        if name == HEADER_SOURCE_TIMESTAMP_NS
        else (name, value)
        for name, value in record.headers
    ]

    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=headers, value=record.value)


def test_decode_negative_timestamp_header_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (HEADER_SOURCE_TIMESTAMP_NS, b"-5")
        if name == HEADER_SOURCE_TIMESTAMP_NS
        else (name, value)
        for name, value in record.headers
    ]

    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=headers, value=record.value)


def test_decode_unknown_envelope_version_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (HEADER_VERSION, b"v99") if name == HEADER_VERSION else (name, value)
        for name, value in record.headers
    ]

    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=headers, value=record.value)


def test_decode_unknown_encoding_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (HEADER_ENCODING, b"protobuf") if name == HEADER_ENCODING else (name, value)
        for name, value in record.headers
    ]

    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=headers, value=record.value)


def test_decode_missing_value_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)

    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=record.headers, value=None)


def test_decode_non_utf8_header_raises() -> None:
    envelope = _envelope()
    record = encode_envelope(envelope)
    headers = [
        (HEADER_CHANNEL, b"\xff\xfe\x00") if name == HEADER_CHANNEL else (name, value)
        for name, value in record.headers
    ]

    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(headers=headers, value=record.value)


def test_envelope_decode_error_includes_topic_partition_offset_when_given() -> None:
    error = EnvelopeDecodeError(
        "boom", topic="sceneops.robot.telemetry.v1", partition=2, offset=17
    )
    assert "sceneops.robot.telemetry.v1:2@17" in str(error)


def test_envelope_decode_error_omits_location_when_not_given() -> None:
    error = EnvelopeDecodeError("boom")
    assert str(error) == "boom"
