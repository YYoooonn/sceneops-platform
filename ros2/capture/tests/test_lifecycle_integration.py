"""RunScopedCapture (capture_consumer.run_capture) against REAL Kafka
with lifecycle control events enabled (Phase 7.2.1) -- no monkeypatched
consumer, matching test_multi_robot_run_integration.py's own
convention.

Proves the compatibility fix end to end against a real broker: a
RUN_START/telemetry/RUN_END stream (exactly what a lifecycle-events-
enabled StreamingBridgeNode publishes) is captured successfully by the
UNCHANGED RunScopedCapture entry point, producing a valid MCAP with no
control-channel records.

``test_real_bridge_run_is_run_start_telemetry_run_end_and_the_receipt_spans_it``
is the owner of the lifecycle envelope of a streamed run: the real
StreamingBridgeNode publishes through its real Kafka producer, and the topic
holds exactly one RUN_START, the run's telemetry records and one RUN_END, in
that order, which the capture receipt's offset range covers end to end. The
transport-equivalence journey reads registered recordings and does not observe
Kafka, so this is where the invariant is pinned.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

import pytest
import rclpy
from builtin_interfaces.msg import Time
from nav_msgs.msg import Odometry
from rclpy.serialization import serialize_message

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "nodes"))

from mcap.reader import make_reader  # noqa: E402
from sceneops_core.robots.capture_receipt import (  # noqa: E402
    FinalizationReason,
    load_canonical_capture_receipt,
)
from sceneops_core.streaming import (  # noqa: E402
    DEFAULT_REGISTRY,
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    build_control_envelope,
    is_control_envelope,
    parse_run_event,
)
from sceneops_streaming import (  # noqa: E402
    KafkaTelemetryConsumer,
    KafkaTelemetryProducer,
    StreamingSettings,
)

from capture_consumer import run_capture  # noqa: E402
from streaming_bridge_node import (  # noqa: E402
    StreamingBridgeNode,
    _AsyncProducerBridge,
)


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


@pytest.fixture(scope="module")
def ros_context():
    initialized_here = not rclpy.ok()
    if initialized_here:
        rclpy.init()
    yield
    if initialized_here:
        rclpy.shutdown()


def _odometry(sequence: int) -> Odometry:
    message = Odometry()
    message.header.stamp = Time(sec=1_700_000_000 + sequence, nanosec=0)
    message.header.frame_id = "odom"
    message.pose.pose.position.x = float(sequence)
    message.pose.pose.orientation.w = 1.0
    return message


async def _records_of_run(settings: StreamingSettings, robot_run_id: str) -> list[dict]:
    """The run's records on the topic, in offset order, read from the beginning by
    a throwaway consumer group that commits nothing (so it cannot disturb the
    capture's own run-scoped group)."""
    consumer = KafkaTelemetryConsumer(
        settings=settings,
        group_id=f"lifecycle-audit-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    records: list[dict] = []
    last_progress = time.monotonic()
    try:
        while time.monotonic() - last_progress < 15.0:
            consumed = await consumer.poll(1.0)
            if consumed is None:
                continue
            last_progress = time.monotonic()
            envelope = consumed.envelope
            if envelope.robot_run_id != robot_run_id:
                continue
            event = parse_run_event(envelope) if is_control_envelope(envelope) else None
            records.append(
                {
                    "partition": consumed.partition,
                    "offset": consumed.offset,
                    "control": event,
                    "channel": envelope.channel,
                    "sequence": envelope.sequence_number,
                }
            )
            if event is RunEventType.RUN_END:
                break
    finally:
        await consumer.close()
    return records


def test_real_bridge_run_is_run_start_telemetry_run_end_and_the_receipt_spans_it(
    tmp_path, ros_context
) -> None:
    """The lifecycle envelope of a streamed run, end to end on a real broker.

    The bridge publishes ``RUN_START`` before its first telemetry record and
    ``RUN_END`` after its last, each with the run's own key and its own sequence
    space. Capture validates them, never writes them to the MCAP, and finalizes on
    the explicit ``RUN_END``; the receipt's Kafka offsets therefore span
    ``message_count + 2`` records, from ``RUN_START`` to ``RUN_END``."""
    invocation = uuid.uuid4().hex[:10]
    robot_id = f"robot-{invocation}"
    robot_run_id = f"lifecycle-bridge-{invocation}"
    settings = StreamingSettings(telemetry_topic=_unique_test_topic())
    telemetry_count = 7
    odometry_spec = next(spec for spec in DEFAULT_REGISTRY if spec.topic == "/vehicle/odom")

    node = StreamingBridgeNode(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        producer_bridge=_AsyncProducerBridge(settings=settings),
        emit_lifecycle_events=True,
    )
    try:
        for sequence in range(telemetry_count):
            node._handle_message(odometry_spec, serialize_message(_odometry(sequence)))
        node.shutdown()  # RUN_END, then flush and close the one producer
    finally:
        node.destroy_node()
    assert (node.published_count, node.failed_count) == (telemetry_count, 0)

    # A capture that never saw RUN_END would end at this deadline and record a
    # different finalization reason below.
    deadline = time.monotonic() + 60
    result = asyncio.run(
        run_capture(
            settings=settings,
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            output_root=tmp_path,
            stop_condition=lambda _count: (
                FinalizationReason.IDLE_TIMEOUT if time.monotonic() > deadline else False
            ),
            stop_on_run_end=True,
            poll_timeout_seconds=2.0,
        )
    )

    records = asyncio.run(_records_of_run(settings, robot_run_id))
    control = [r for r in records if r["control"] is not None]
    telemetry = [r for r in records if r["control"] is None]
    assert [r["control"] for r in control] == [RunEventType.RUN_START, RunEventType.RUN_END]
    assert records[0] is control[0] and records[-1] is control[1]
    assert len(telemetry) == telemetry_count
    assert [r["sequence"] for r in telemetry] == list(range(telemetry_count))
    assert [r["sequence"] for r in control] == [0, 1]
    assert {r["partition"] for r in records} == {records[0]["partition"]}
    # The only run on its topic: its records are one contiguous offset range.
    offsets = [r["offset"] for r in records]
    assert offsets == list(range(offsets[0], offsets[0] + telemetry_count + 2))

    receipt = load_canonical_capture_receipt(result.receipt_path.read_bytes())
    assert receipt.finalization.reason is FinalizationReason.EXPLICIT_RUN_END
    assert receipt.message_count == telemetry_count
    assert receipt.kafka.partition == records[0]["partition"]
    assert (receipt.kafka.first_offset, receipt.kafka.last_offset) == (
        control[0]["offset"],
        control[1]["offset"],
    )
    assert receipt.kafka.last_offset - receipt.kafka.first_offset + 1 == telemetry_count + 2
    assert (receipt.kafka.first_sequence, receipt.kafka.last_sequence) == (
        0,
        telemetry_count - 1,
    )
    assert _mcap_channels(result.path) == {"/vehicle/odom"}
