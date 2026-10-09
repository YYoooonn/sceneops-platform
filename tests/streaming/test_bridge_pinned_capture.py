"""Bridge -> pinned Capture on a multi-partition topic, against a real broker.

The real ``StreamingBridgeNode`` publishes through its real Kafka producer and
reports the broker-acknowledged position of its ``RUN_START``
(``node.run_start_position``, what a launcher passes as ``--start-position``).
Capture is then pinned to exactly that position (``assign()``, no group join).
Several runs share one 3-partition topic, so their records are interleaved and
spread over partitions; each capture must stay on its own run's partition.

Also pins the failure and race boundaries of the hand-off: a Capture launched
after the whole run, a bridge that dies after RUN_START, a RUN_START that is
never acknowledged, and the no-position fallback reading the same run.

Spans two apps (bridge + Capture), so it lives here. Runs only in a ROS 2
container with the bridge source on PYTHONPATH (``make streaming-test``) and
needs a running Kafka broker (``make streaming-up``).
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
import rclpy
from builtin_interfaces.msg import Time
from confluent_kafka.admin import AdminClient, NewTopic
from nav_msgs.msg import Odometry
from rclpy.serialization import serialize_message

from sceneops_recording.capture.consumer import (
    RunEndNotObservedError,
    run_capture,
)
from sceneops_recording.capture_receipt import (
    FinalizationReason,
    load_canonical_capture_receipt,
)
from sceneops_streaming import (
    DEFAULT_REGISTRY,
    KafkaStartPosition,
    KafkaTelemetryConsumer,
    RunEventType,
    StreamingSettings,
    is_control_envelope,
    parse_run_event,
)
from sceneops_streaming_bridge.node import (
    RunStartError,
    StreamingBridgeNode,
    _AsyncProducerBridge,
)

PARTITIONS = 3
RUNS = 6
TELEMETRY_PER_RUN = 12

_ADMIN = AdminClient({"bootstrap.servers": StreamingSettings().bootstrap_servers})
_ODOMETRY = next(spec for spec in DEFAULT_REGISTRY if spec.topic == "/vehicle/odom")


@pytest.fixture(scope="module")
def ros_context():
    initialized_here = not rclpy.ok()
    if initialized_here:
        rclpy.init()
    yield
    if initialized_here:
        rclpy.shutdown()


@pytest.fixture
def settings():
    topic = f"sceneops.robot.telemetry.pinned-bridge-test.{uuid.uuid4().hex[:12]}.v1"
    _ADMIN.create_topics([NewTopic(topic, num_partitions=PARTITIONS, replication_factor=1)])[
        topic
    ].result(timeout=30)
    yield StreamingSettings(telemetry_topic=topic)
    _ADMIN.delete_topics([topic])


def _odometry(sequence: int) -> bytes:
    message = Odometry()
    message.header.stamp = Time(sec=1_700_000_000 + sequence, nanosec=0)
    message.header.frame_id = "odom"
    message.pose.pose.position.x = float(sequence)
    message.pose.pose.orientation.w = 1.0
    return serialize_message(message)


def _start_bridge(settings: StreamingSettings, run_id: str) -> StreamingBridgeNode:
    return StreamingBridgeNode(
        robot_id=f"robot-{run_id}",
        robot_run_id=run_id,
        producer_bridge=_AsyncProducerBridge(settings=settings),
        emit_lifecycle_events=True,
    )


def _bridge_telemetry(node: StreamingBridgeNode, count: int) -> None:
    for sequence in range(count):
        node._handle_message(_ODOMETRY, _odometry(sequence))


def _finish(node: StreamingBridgeNode) -> None:
    node.shutdown()
    node.destroy_node()


async def _capture(settings, run_id, root, start, **kwargs):
    return await run_capture(
        settings=settings,
        robot_id=f"robot-{run_id}",
        robot_run_id=run_id,
        output_root=root,
        stop_on_run_end=True,
        idle_timeout_seconds=60,
        poll_timeout_seconds=1.0,
        start=start,
        **kwargs,
    )


async def _records_by_run(settings: StreamingSettings, run_ids: set[str]) -> dict:
    """Every record of the given runs, read from the beginning by a throwaway
    group that commits nothing: (partition, offset, event) in offset order."""
    consumer = KafkaTelemetryConsumer(
        settings=settings,
        group_id=f"audit-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    records: dict[str, list] = {run_id: [] for run_id in run_ids}
    last_progress = time.monotonic()
    try:
        while time.monotonic() - last_progress < 10.0:
            consumed = await consumer.poll(1.0)
            if consumed is None:
                continue
            last_progress = time.monotonic()
            envelope = consumed.envelope
            if envelope.robot_run_id in records:
                event = (
                    parse_run_event(envelope) if is_control_envelope(envelope) else None
                )
                records[envelope.robot_run_id].append(
                    (consumed.partition, consumed.offset, event)
                )
    finally:
        await consumer.close()
    return records


def test_interleaved_runs_on_a_multi_partition_topic_each_capture_their_own_partition(
    tmp_path, settings, ros_context
) -> None:
    """Several runs interleave on three partitions. Each run's acknowledged
    RUN_START position is the partition its telemetry and RUN_END use, and a
    Capture pinned to it records exactly that run, from RUN_START, even when it
    is launched while the run is still being bridged."""
    run_ids = [f"pinned-bridge-{i}-{uuid.uuid4().hex[:8]}" for i in range(RUNS)]
    nodes = {run_id: _start_bridge(settings, run_id) for run_id in run_ids}
    positions = {run_id: nodes[run_id].run_start_position for run_id in run_ids}
    assert all(isinstance(p, KafkaStartPosition) for p in positions.values())
    assert all(p.topic == settings.telemetry_topic for p in positions.values())
    # Not all on one partition, or the pinning below would prove nothing.
    assert len({p.partition for p in positions.values()}) >= 2

    async def scenario():
        # Captures are launched with the hint while the runs are still being
        # bridged; telemetry of all runs interleaves in a worker thread.
        captures = [
            asyncio.create_task(_capture(settings, run_id, tmp_path / run_id, positions[run_id]))
            for run_id in run_ids
        ]

        def bridge_interleaved() -> None:
            for sequence in range(TELEMETRY_PER_RUN):
                for run_id in run_ids:
                    nodes[run_id]._handle_message(_ODOMETRY, _odometry(sequence))
            for run_id in run_ids:
                _finish(nodes[run_id])

        await asyncio.to_thread(bridge_interleaved)
        return await asyncio.gather(*captures)

    results = dict(zip(run_ids, asyncio.run(scenario())))
    records = asyncio.run(_records_by_run(settings, set(run_ids)))

    for run_id in run_ids:
        position = positions[run_id]
        run_records = records[run_id]
        # RUN_START, telemetry and RUN_END all live on the partition RUN_START
        # reported, and RUN_START is exactly at the reported offset.
        assert {partition for partition, _o, _e in run_records} == {position.partition}
        assert run_records[0] == (position.partition, position.offset, RunEventType.RUN_START)
        assert run_records[-1][2] is RunEventType.RUN_END
        assert len(run_records) == TELEMETRY_PER_RUN + 2

        receipt = load_canonical_capture_receipt(results[run_id].receipt_path.read_bytes())
        assert receipt.finalization.reason is FinalizationReason.EXPLICIT_RUN_END
        assert receipt.message_count == TELEMETRY_PER_RUN
        assert receipt.kafka.partition == position.partition
        assert receipt.kafka.first_offset == position.offset
        assert receipt.kafka.last_offset == run_records[-1][1]


def test_capture_launched_after_the_whole_run_still_records_it_from_run_start(
    tmp_path, settings, ros_context
) -> None:
    """The hint is a position in the log, not a rendezvous: a late Capture reads
    the complete run from RUN_START. The no-hint fallback reads the same run and
    produces the same recording."""
    run_id = f"pinned-late-{uuid.uuid4().hex[:8]}"
    node = _start_bridge(settings, run_id)
    position = node.run_start_position
    _bridge_telemetry(node, TELEMETRY_PER_RUN)
    _finish(node)

    pinned = asyncio.run(_capture(settings, run_id, tmp_path / "pinned", position))
    fallback = asyncio.run(_capture(settings, run_id, tmp_path / "fallback", None))

    assert pinned.message_count == TELEMETRY_PER_RUN
    assert (pinned.partition, pinned.first_offset) == (position.partition, position.offset)
    assert (fallback.partition, fallback.first_offset, fallback.last_offset) == (
        pinned.partition,
        pinned.first_offset,
        pinned.last_offset,
    )
    assert fallback.message_count == pinned.message_count


def test_bridge_killed_after_run_start_is_refused_by_the_pinned_capture(
    tmp_path, settings, ros_context
) -> None:
    """RUN_START was acknowledged, then the bridge died before any telemetry or
    RUN_END. A Capture started from the (valid) hint observes RUN_START and
    ends through the existing inactivity guard; nothing is finalized."""
    run_id = f"pinned-dead-{uuid.uuid4().hex[:8]}"
    node = _start_bridge(settings, run_id)
    position = node.run_start_position
    node.destroy_node()  # no shutdown(): no RUN_END ever follows

    with pytest.raises(RunEndNotObservedError):
        asyncio.run(
            run_capture(
                settings=settings,
                robot_id=f"robot-{run_id}",
                robot_run_id=run_id,
                output_root=tmp_path,
                stop_on_run_end=True,
                idle_timeout_seconds=3,
                poll_timeout_seconds=0.5,
                start=position,
            )
        )
    assert not (tmp_path / run_id).exists()


def test_unacknowledged_run_start_fails_startup_and_yields_no_position(
    settings, ros_context
) -> None:
    """With no broker to acknowledge RUN_START the bridge raises instead of
    starting: there is no position to hand to a launcher."""
    unreachable = StreamingSettings(
        bootstrap_servers="127.0.0.1:1", telemetry_topic=settings.telemetry_topic
    )
    started = time.monotonic()
    with pytest.raises(RunStartError, match="not acknowledged"):
        StreamingBridgeNode(
            robot_id="robot-x",
            robot_run_id=f"pinned-unacked-{uuid.uuid4().hex[:8]}",
            producer_bridge=_AsyncProducerBridge(
                settings=unreachable, publish_timeout_seconds=1.0
            ),
            publish_timeout_seconds=1.0,
            emit_lifecycle_events=True,
        )
    assert time.monotonic() - started < 15
