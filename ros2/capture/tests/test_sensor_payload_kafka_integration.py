"""Realistic sensor payload sizes through REAL Kafka, end to end.

The broker and the producer/consumer run with their stock size limits (no
override anywhere in compose or settings). The sizes below are the largest
payloads in the nuScenes v1.0-mini sweeps and samples this stack replays:

    camera JPEG (CompressedImage)   298,656 B   (largest of 2,342 front frames)
    lidar sweep (PointCloud2)       696,320 B   (largest of 3,935 sweeps)

A payload past the producer's message limit must fail loudly at publish,
never truncate or vanish: that is the failure mode the bridge turns into a
counted, logged error.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcap.reader import make_reader  # noqa: E402
from sceneops_core.streaming import (  # noqa: E402
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    build_channel_registry,
    build_control_envelope,
)
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings  # noqa: E402

from capture_consumer import run_capture  # noqa: E402

SURROUND = (
    Path(__file__).resolve().parents[2] / "channels" / "surround-camera-lidar.json"
)
CAMERA_BYTES = 298_656
LIDAR_BYTES = 696_320


def _envelope(
    run: str, robot: str, seq: int, channel: str, message_type: str, payload: bytes
):
    return TelemetryEnvelope(
        robot_id=robot,
        robot_run_id=run,
        channel=channel,
        message_type=message_type,
        source_timestamp_ns=1_532_402_927_000_000_000 + seq,
        ingest_timestamp_ns=1_700_000_000_000_000_000 + seq,
        sequence_number=seq,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=payload,
    )


def test_camera_and_lidar_sized_payloads_round_trip_through_capture(tmp_path) -> None:
    invocation = uuid.uuid4().hex[:10]
    run, robot = f"sensor-payload-{invocation}", f"robot-{invocation}"
    topic = f"sceneops.robot.telemetry.sensor-payload-test.{invocation}.v1"
    registry = build_channel_registry([SURROUND])
    payloads = [
        (
            "/lidar/top/points",
            "sensor_msgs/msg/PointCloud2",
            bytes((i * 7 + j) % 256 for j in range(LIDAR_BYTES)),
        )
        if i % 2 == 0
        else (
            "/camera/front/image/compressed",
            "sensor_msgs/msg/CompressedImage",
            bytes((i * 13 + j) % 256 for j in range(CAMERA_BYTES)),
        )
        for i in range(12)
    ]

    async def publish() -> None:
        producer = KafkaTelemetryProducer(
            settings=StreamingSettings(telemetry_topic=topic)
        )
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_START, robot_id=robot, robot_run_id=run
            )
        )
        for seq, (channel, message_type, payload) in enumerate(payloads):
            await producer.publish(
                _envelope(run, robot, seq, channel, message_type, payload)
            )
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id=robot,
                robot_run_id=run,
                sequence_number=1,
            )
        )
        await producer.close()

    asyncio.run(publish())
    result = asyncio.run(
        run_capture(
            settings=StreamingSettings(telemetry_topic=topic),
            robot_id=robot,
            robot_run_id=run,
            output_root=tmp_path,
            stop_condition=lambda count: False,
            stop_on_run_end=True,
            poll_timeout_seconds=2.0,
            registry=registry,
        )
    )

    assert result.message_count == len(payloads)
    assert result.per_channel_counts == {
        "/lidar/top/points": 6,
        "/camera/front/image/compressed": 6,
    }
    with open(result.path, "rb") as f:
        recorded = [m.data for _s, _c, m in make_reader(f).iter_messages()]
    assert recorded == [payload for _c, _t, payload in payloads]


def test_payload_beyond_the_message_limit_fails_loudly_at_publish() -> None:
    topic = f"sceneops.robot.telemetry.oversize-test.{uuid.uuid4().hex[:10]}.v1"
    oversize = _envelope(
        "run-oversize",
        "robot-oversize",
        0,
        "/lidar/top/points",
        "sensor_msgs/msg/PointCloud2",
        bytes(1_500_000),
    )

    async def attempt() -> None:
        producer = KafkaTelemetryProducer(
            settings=StreamingSettings(telemetry_topic=topic)
        )
        try:
            await producer.publish(oversize)
            await producer.flush()
        finally:
            try:
                await producer.close()
            except Exception:
                pass

    with pytest.raises(Exception, match="(?i)too large"):
        asyncio.run(attempt())


def test_zero_source_timestamp_round_trips_through_real_kafka_and_capture(
    tmp_path,
) -> None:
    """A zero-stamped static transform (source timestamp 0) is a legal
    envelope: it survives the Kafka wire and is recorded unchanged."""
    invocation = uuid.uuid4().hex[:10]
    run, robot = f"zero-stamp-{invocation}", f"robot-{invocation}"
    topic = f"sceneops.robot.telemetry.zero-stamp-test.{invocation}.v1"
    payload = b"\x00\x01\x00\x00" + bytes(12)

    async def publish() -> None:
        producer = KafkaTelemetryProducer(
            settings=StreamingSettings(telemetry_topic=topic)
        )
        envelope = _envelope(
            run, robot, 0, "/tf_static", "tf2_msgs/msg/TFMessage", payload
        )
        await producer.publish(envelope.model_copy(update={"source_timestamp_ns": 0}))
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id=robot,
                robot_run_id=run,
                sequence_number=0,
            )
        )
        await producer.close()

    asyncio.run(publish())
    result = asyncio.run(
        run_capture(
            settings=StreamingSettings(telemetry_topic=topic),
            robot_id=robot,
            robot_run_id=run,
            output_root=tmp_path,
            stop_condition=lambda count: False,
            stop_on_run_end=True,
            poll_timeout_seconds=2.0,
        )
    )

    assert result.per_channel_counts == {"/tf_static": 1}
    with open(result.path, "rb") as f:
        [message] = [m for _s, _c, m in make_reader(f).iter_messages()]
    assert message.data == payload
