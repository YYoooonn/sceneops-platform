"""RunScopedCapture (capture_consumer.run_capture) against REAL Kafka
with lifecycle control events enabled (Phase 7.2.1) -- no monkeypatched
consumer, matching test_multi_robot_run_integration.py's own
convention.

Proves the compatibility fix end to end against a real broker: a
RUN_START/telemetry/RUN_END stream (exactly what a lifecycle-events-
enabled StreamingBridgeNode publishes) is captured successfully by the
UNCHANGED RunScopedCapture entry point, producing a valid MCAP with no
control-channel records.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcap.reader import make_reader  # noqa: E402
from sceneops_core.streaming import (  # noqa: E402
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    build_control_envelope,
)
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings  # noqa: E402

from capture_consumer import run_capture  # noqa: E402


def _unique_test_topic() -> str:
    # Same reasoning as test_router_integration.py's own helper -- a
    # fresh, disposable, per-invocation topic so this test never has to
    # scan unrelated accumulated history from other local/CI runs.
    return f"sceneops.robot.telemetry.lifecycle-capture-test.{uuid.uuid4().hex[:12]}.v1"


def _envelope(*, robot_run_id: str, robot_id: str, sequence_number: int) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_700_000_000_000_000_000 + sequence_number,
        ingest_timestamp_ns=1_700_000_000_500_000_000 + sequence_number,
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=bytes([0x00, 0x01, 0x00, 0x00]) + b"x" * 16,
    )


def _mcap_channels(path) -> set[str]:
    with open(path, "rb") as f:
        reader = make_reader(f)
        return {channel.topic for _schema, channel, _message in reader.iter_messages()}


def test_run_capture_handles_real_lifecycle_enabled_stream(tmp_path) -> None:
    invocation = uuid.uuid4().hex[:10]
    robot_id = f"robot-{invocation}"
    robot_run_id = f"lifecycle-capture-{invocation}"
    topic = _unique_test_topic()
    telemetry_count = 5

    async def _publish():
        settings = StreamingSettings(telemetry_topic=topic)
        producer = KafkaTelemetryProducer(settings=settings)
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_START, robot_id=robot_id, robot_run_id=robot_run_id
            )
        )
        for seq in range(telemetry_count):
            await producer.publish(
                _envelope(robot_run_id=robot_run_id, robot_id=robot_id, sequence_number=seq)
            )
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                sequence_number=1,
            )
        )
        await producer.flush()
        await producer.close()

    asyncio.run(_publish())

    result = asyncio.run(
        run_capture(
            settings=StreamingSettings(telemetry_topic=topic),
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            output_root=tmp_path,
            # Matches exactly how the bridge's own published_count
            # (telemetry-only) is used as --max-messages in practice --
            # control events never count toward writer.stats.message_count.
            stop_condition=lambda count: count >= telemetry_count,
            poll_timeout_seconds=2.0,
        )
    )

    assert result.message_count == telemetry_count
    assert result.first_sequence == 0
    assert result.last_sequence == telemetry_count - 1
    assert _mcap_channels(result.path) == {"/vehicle/odom"}


def test_run_capture_handles_real_lifecycle_events_interleaved_with_another_run(
    tmp_path,
) -> None:
    """Two runs, both lifecycle-enabled, interleaved on the real
    broker/topic/partition -- RunScopedCapture for the TARGET run must
    still isolate it from the OTHER run's own control events and
    telemetry, exactly as it already does for pure telemetry
    (test_multi_robot_run_integration.py)."""
    invocation = uuid.uuid4().hex[:10]
    target_id = f"lifecycle-iso-target-{invocation}"
    other_id = f"lifecycle-iso-other-{invocation}"
    topic = _unique_test_topic()

    async def _publish():
        settings = StreamingSettings(telemetry_topic=topic)
        producer = KafkaTelemetryProducer(settings=settings)

        async def start(run_id):
            await producer.publish(
                build_control_envelope(
                    event_type=RunEventType.RUN_START,
                    robot_id=f"robot-{run_id}",
                    robot_run_id=run_id,
                )
            )

        async def end(run_id):
            await producer.publish(
                build_control_envelope(
                    event_type=RunEventType.RUN_END,
                    robot_id=f"robot-{run_id}",
                    robot_run_id=run_id,
                    sequence_number=1,
                )
            )

        async def telem(run_id, seq):
            await producer.publish(
                _envelope(robot_run_id=run_id, robot_id=f"robot-{run_id}", sequence_number=seq)
            )

        await start(target_id)
        await start(other_id)
        await telem(target_id, 0)
        await telem(other_id, 0)
        await telem(other_id, 1)
        await telem(target_id, 1)
        await end(target_id)
        await telem(other_id, 2)
        await end(other_id)
        await producer.flush()
        await producer.close()

    asyncio.run(_publish())

    result = asyncio.run(
        run_capture(
            settings=StreamingSettings(telemetry_topic=topic),
            robot_id=f"robot-{target_id}",
            robot_run_id=target_id,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 2,
            poll_timeout_seconds=2.0,
        )
    )

    assert result.message_count == 2
    assert result.first_sequence == 0
    assert result.last_sequence == 1
    assert _mcap_channels(result.path) == {"/vehicle/odom"}
