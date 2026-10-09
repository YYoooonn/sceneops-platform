"""Unit tests for sceneops_streaming.control (Phase 7.2 run
lifecycle control events). Pure schema/wire-shape checks -- no Kafka
broker involved.
"""

from __future__ import annotations

import json

import pytest

from sceneops_streaming.constants import SESSION_CONTROL_CHANNEL
from sceneops_streaming import (
    EnvelopeEncoding,
    RunEndPayloadError,
    RunEventType,
    TelemetryEnvelope,
    build_control_envelope,
    is_control_envelope,
    parse_run_end_telemetry_count,
    parse_run_event,
)


def _telemetry_envelope(**overrides: object) -> TelemetryEnvelope:
    kwargs = dict(
        robot_id="robot-1",
        robot_run_id="robotrun-1",
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_700_000_000_000_000_000,
        ingest_timestamp_ns=1_700_000_000_500_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=b"\x00\x01",
    )
    kwargs.update(overrides)
    return TelemetryEnvelope(**kwargs)


def test_build_control_envelope_uses_reserved_channel_and_json_encoding() -> None:
    env = build_control_envelope(
        event_type=RunEventType.RUN_START, robot_id="robot-1", robot_run_id="run-1"
    )
    assert env.channel == SESSION_CONTROL_CHANNEL
    assert env.encoding == EnvelopeEncoding.JSON
    assert env.robot_id == "robot-1"
    assert env.robot_run_id == "run-1"
    assert env.source_timestamp_ns > 0


def test_run_start_and_run_end_have_distinct_message_types() -> None:
    start = build_control_envelope(
        event_type=RunEventType.RUN_START, robot_id="r", robot_run_id="run-1"
    )
    end = build_control_envelope(
        event_type=RunEventType.RUN_END,
        robot_id="r",
        robot_run_id="run-1",
        telemetry_count=0,
    )
    assert start.message_type != end.message_type


def test_is_control_envelope_true_only_for_reserved_channel() -> None:
    control = build_control_envelope(
        event_type=RunEventType.RUN_START, robot_id="r", robot_run_id="run-1"
    )
    telemetry = _telemetry_envelope()
    assert is_control_envelope(control) is True
    assert is_control_envelope(telemetry) is False


def test_parse_run_event_round_trips_for_both_event_types() -> None:
    for event_type, count in (
        (RunEventType.RUN_START, None),
        (RunEventType.RUN_END, 3),
    ):
        env = build_control_envelope(
            event_type=event_type,
            robot_id="r",
            robot_run_id="run-1",
            telemetry_count=count,
        )
        assert parse_run_event(env) is event_type


def test_parse_run_event_returns_none_for_non_control_envelope() -> None:
    assert parse_run_event(_telemetry_envelope()) is None


def test_parse_run_event_returns_none_for_unrecognized_message_type_on_control_channel() -> (
    None
):
    # Forward-compat: a future/unknown control message_type is ignored,
    # never fatal -- simulated here via a hand-built envelope on the
    # reserved channel with a message_type this module doesn't know.
    env = TelemetryEnvelope(
        robot_id="r",
        robot_run_id="run-1",
        channel=SESSION_CONTROL_CHANNEL,
        message_type="sceneops/control/SomeFutureEvent",
        source_timestamp_ns=1_700_000_000_000_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.JSON,
        payload=b"{}",
    )
    assert is_control_envelope(env) is True
    assert parse_run_event(env) is None


def test_control_envelope_survives_real_wire_encode_decode_round_trip() -> None:
    from sceneops_streaming.wire import decode_envelope, encode_envelope

    original = build_control_envelope(
        event_type=RunEventType.RUN_END,
        robot_id="robot-9",
        robot_run_id="run-9",
        reason="graceful shutdown",
        telemetry_count=7,
    )
    record = encode_envelope(original)
    assert record.key == b"run-9"  # Kafka key = robot_run_id, unchanged

    decoded = decode_envelope(headers=record.headers, value=record.value)
    assert decoded == original
    assert parse_run_event(decoded) is RunEventType.RUN_END
    assert parse_run_end_telemetry_count(decoded) == 7


def _run_end(count: int | None = 0, **kw) -> TelemetryEnvelope:
    return build_control_envelope(
        event_type=RunEventType.RUN_END,
        robot_id="r",
        robot_run_id="run-1",
        telemetry_count=count,
        **kw,
    )


@pytest.mark.parametrize("count", [0, 1, 2000])
def test_run_end_declares_telemetry_count(count: int) -> None:
    env = _run_end(count)
    assert json.loads(env.payload) == {"telemetry_count": count}
    assert parse_run_end_telemetry_count(env) == count


def test_run_end_payload_keeps_reason_beside_the_count() -> None:
    env = _run_end(4, reason="shutdown")
    assert json.loads(env.payload) == {"reason": "shutdown", "telemetry_count": 4}
    assert parse_run_end_telemetry_count(env) == 4


def test_run_end_without_telemetry_count_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="must declare telemetry_count"):
        _run_end(None)


@pytest.mark.parametrize("count", [-1, True])
def test_run_end_rejects_an_invalid_count_at_build(count) -> None:
    with pytest.raises(ValueError, match="telemetry_count"):
        _run_end(count)


def test_run_start_does_not_carry_telemetry_count() -> None:
    with pytest.raises(ValueError, match="does not carry telemetry_count"):
        build_control_envelope(
            event_type=RunEventType.RUN_START,
            robot_id="r",
            robot_run_id="run-1",
            telemetry_count=0,
        )
    start = build_control_envelope(
        event_type=RunEventType.RUN_START, robot_id="r", robot_run_id="run-1"
    )
    assert json.loads(start.payload) == {}


def _raw_run_end(payload: bytes) -> TelemetryEnvelope:
    return _run_end(0).model_copy(update={"payload": payload})


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",  # a RUN_END that declares nothing
        b"not json",
        b"[]",
        b'{"telemetry_count": "3"}',
        b'{"telemetry_count": 2.0}',
        b'{"telemetry_count": true}',
        b'{"telemetry_count": -1}',
        b'{"telemetry_count": null}',
    ],
)
def test_parse_run_end_rejects_a_payload_without_a_valid_count(payload) -> None:
    with pytest.raises(RunEndPayloadError):
        parse_run_end_telemetry_count(_raw_run_end(payload))


def test_parse_run_end_rejects_other_envelopes() -> None:
    start = build_control_envelope(
        event_type=RunEventType.RUN_START, robot_id="r", robot_run_id="run-1"
    )
    with pytest.raises(RunEndPayloadError):
        parse_run_end_telemetry_count(start)
    with pytest.raises(RunEndPayloadError):
        parse_run_end_telemetry_count(_telemetry_envelope())
