"""Unit tests for TelemetryEnvelope. Pure schema validation -- no Kafka
broker involved; Kafka-backed behavior lives in sceneops-streaming's own
tests / `make smoke-streaming`.
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from sceneops_core.streaming import (
    EnvelopeEncoding,
    TelemetryEnvelope,
    TelemetryEnvelopeVersion,
)


def _valid_kwargs(**overrides: object) -> dict:
    kwargs = dict(
        robot_id="robot-1",
        robot_run_id="robotrun-1",
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_700_000_000_000_000_000,
        ingest_timestamp_ns=1_700_000_000_500_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=b"\x00\x01\xff\xfe",
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_envelope_round_trips_all_fields() -> None:
    envelope = TelemetryEnvelope(**_valid_kwargs())

    assert envelope.version == TelemetryEnvelopeVersion.V1
    assert envelope.robot_id == "robot-1"
    assert envelope.robot_run_id == "robotrun-1"
    assert envelope.channel == "/vehicle/odom"
    assert envelope.message_type == "nav_msgs/msg/Odometry"
    assert envelope.source_timestamp_ns == 1_700_000_000_000_000_000
    assert envelope.ingest_timestamp_ns == 1_700_000_000_500_000_000
    assert envelope.sequence_number == 0
    assert envelope.encoding == EnvelopeEncoding.ROS2_CDR
    assert envelope.payload == b"\x00\x01\xff\xfe"


def test_payload_is_binary_first_not_text() -> None:
    # Bytes that are not valid UTF-8 -- must survive untouched, proving the
    # envelope never tries to treat payload as text.
    payload = bytes([0xFF, 0xFE, 0x00, 0x80])
    envelope = TelemetryEnvelope(**_valid_kwargs(payload=payload))
    assert envelope.payload == payload


@pytest.mark.parametrize(
    "field", ["robot_id", "robot_run_id", "channel", "message_type"]
)
def test_missing_required_identity_field_rejected(field: str) -> None:
    kwargs = _valid_kwargs()
    del kwargs[field]
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**kwargs)


@pytest.mark.parametrize(
    "field", ["robot_id", "robot_run_id", "channel", "message_type"]
)
def test_empty_required_identity_field_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**_valid_kwargs(**{field: ""}))


def test_missing_encoding_rejected() -> None:
    kwargs = _valid_kwargs()
    del kwargs["encoding"]
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**kwargs)


def test_unknown_encoding_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**_valid_kwargs(encoding="protobuf"))


def test_negative_source_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**_valid_kwargs(source_timestamp_ns=-1))


def test_zero_source_timestamp_is_a_legal_verbatim_value() -> None:
    """A source message may carry a zero (unstamped) timestamp, e.g. a
    /tf_static transform. The envelope passes it through; it is the
    producer's decision which channels may carry it."""
    envelope = TelemetryEnvelope(**_valid_kwargs(source_timestamp_ns=0))
    assert envelope.source_timestamp_ns == 0
    assert envelope.ingest_timestamp_ns > 0


@pytest.mark.parametrize("bad_value", [0, -1])
def test_invalid_ingest_timestamp_rejected(bad_value: int) -> None:
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**_valid_kwargs(ingest_timestamp_ns=bad_value))


def test_negative_sequence_number_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**_valid_kwargs(sequence_number=-1))


def test_unknown_envelope_version_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryEnvelope(**_valid_kwargs(version="v2"))


def test_default_version_is_v1_when_omitted() -> None:
    kwargs = _valid_kwargs()
    envelope = TelemetryEnvelope(**kwargs)
    assert envelope.version == TelemetryEnvelopeVersion.V1


def test_equal_source_and_ingest_timestamps_are_valid() -> None:
    """source_timestamp_ns and ingest_timestamp_ns are semantically
    distinct clocks (observation time vs. transport-boundary-accept
    time), not values required to differ numerically -- a caller
    supplying the same instant for both must not be rejected."""

    envelope = TelemetryEnvelope(
        **_valid_kwargs(source_timestamp_ns=100, ingest_timestamp_ns=100)
    )
    assert envelope.source_timestamp_ns == 100
    assert envelope.ingest_timestamp_ns == 100
    assert envelope.source_timestamp_ns == envelope.ingest_timestamp_ns


def test_ingest_timestamp_defaults_to_construction_time_when_omitted() -> None:
    before = time.time_ns()
    kwargs = _valid_kwargs()
    del kwargs["ingest_timestamp_ns"]
    envelope = TelemetryEnvelope(**kwargs)
    after = time.time_ns()

    assert before <= envelope.ingest_timestamp_ns <= after


def test_ingest_timestamp_explicit_value_overrides_default() -> None:
    envelope = TelemetryEnvelope(**_valid_kwargs(ingest_timestamp_ns=42))
    assert envelope.ingest_timestamp_ns == 42
