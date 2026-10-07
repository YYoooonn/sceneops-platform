"""Phase 6.6 reliability matrix item 5 / Phase 6.6.1 acceptance
(multi-RobotRun isolation), against the REAL local Kafka broker -- no
monkeypatched consumer, unlike the rest of this test package.

Two things are tested:

  1. Within ONE capture invocation, RunFilter correctly isolates the
     target robot_run_id from interleaved messages belonging to a
     DIFFERENT robot_run_id on the same topic/partition -- the resulting
     MCAP contains only the target run's messages. Unaffected by Phase
     6.6.1 -- this guarantee already existed.

  2. Two SEQUENTIAL, independent capture invocations for interleaved
     runs A and B now get fully independent offset cursors (Phase
     6.6.1's run-scoped consumer groups, group_id.py): each sees its own
     COMPLETE 0..N-1 sequence and writes only its own messages, in
     either processing order. Phase 6.6 found and this phase fixes the
     previous failure mode -- A's capture reading past B's interleaved
     messages no longer silently advances a SHARED group's committed
     offset past them, because A and B no longer share a group at all.

The local dev topic has exactly one partition
(`sceneops.robot.telemetry.v1`, `PartitionCount: 1`), which makes
interleaving on one partition the realistic default, not a rare edge
case -- and confirms this fix is about consumer-group isolation, not
Kafka partition scaling (see group_id.py's own docstring and
docs/history/streaming-reliability-scale-baseline.md's Phase 6.6.1
addendum for the historical-rescan tradeoff this isolation trades for).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcap.reader import make_reader  # noqa: E402
from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope  # noqa: E402
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings  # noqa: E402

from capture_consumer import CAPTURE_CONSUMER_GROUP_ID, run_capture  # noqa: E402
from group_id import derive_capture_group_id  # noqa: E402


def _envelope(*, robot_run_id: str, sequence_number: int) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        robot_id=f"robot-{robot_run_id}",
        robot_run_id=robot_run_id,
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_700_000_000_000_000_000 + sequence_number,
        ingest_timestamp_ns=1_700_000_000_500_000_000 + sequence_number,
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=bytes([0x00, 0x01, 0x00, 0x00])
        + robot_run_id.encode("ascii").ljust(16, b"\x00"),
    )


def _read_mcap_channel_bytes(path) -> list[bytes]:
    with open(path, "rb") as f:
        reader = make_reader(f)
        return [message.data for _s, _c, message in reader.iter_messages()]


def test_run_filter_isolates_target_run_from_interleaved_other_run(tmp_path) -> None:
    """Within one capture invocation: A's messages interleaved with B's on
    the same real topic/partition -- A's finalized MCAP must contain ONLY
    A's messages, never B's, regardless of interleaving order."""
    invocation = uuid.uuid4().hex[:10]
    run_a = f"iso-a-{invocation}"
    run_b = f"iso-b-{invocation}"

    async def _publish_interleaved():
        settings = StreamingSettings()
        producer = KafkaTelemetryProducer(settings=settings)
        # a0, b0, a1, b1, a2 -- A's target (3 messages) is reached before
        # B's last message is even published.
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=0))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=0))
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=1))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=1))
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=2))
        await producer.flush()
        await producer.close()

    asyncio.run(_publish_interleaved())

    result = asyncio.run(
        run_capture(
            settings=StreamingSettings(),
            robot_id=f"robot-{run_a}",
            robot_run_id=run_a,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
            poll_timeout_seconds=2.0,
        )
    )

    assert result.message_count == 3
    payloads = _read_mcap_channel_bytes(result.path)
    assert len(payloads) == 3
    for payload in payloads:
        assert run_a.encode("ascii") in payload
        assert run_b.encode("ascii") not in payload


def test_sequential_independent_captures_each_see_complete_sequence(tmp_path) -> None:
    """Phase 6.6.1 acceptance: A's capture (reading past B's interleaved
    messages to reach its own target) must NOT advance B's committed
    position -- B's later, independent capture still sees its own
    complete 0..N-1 sequence and writes only its own messages. This is
    the exact scenario that used to fail (SequenceIntegrityError on B)
    before run-scoped consumer groups."""
    invocation = uuid.uuid4().hex[:10]
    run_a = f"seq-a-{invocation}"
    run_b = f"seq-b-{invocation}"

    # A and B derive different groups -- confirms the isolation mechanism
    # directly, not just its downstream effect.
    group_a = derive_capture_group_id(base=CAPTURE_CONSUMER_GROUP_ID, robot_run_id=run_a)
    group_b = derive_capture_group_id(base=CAPTURE_CONSUMER_GROUP_ID, robot_run_id=run_b)
    assert group_a != group_b

    async def _publish_interleaved():
        settings = StreamingSettings()
        producer = KafkaTelemetryProducer(settings=settings)
        # a0, b0, a1, b1, b2, a2, b3 -- A only needs 3 (a0,a1,a2); B has 4
        # (b0..b3), and b2 sits BEFORE a2 in publish/offset order, so A's
        # poll loop necessarily reads past b0/b1/b2 to reach a2.
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=0))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=0))
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=1))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=1))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=2))
        await producer.publish(_envelope(robot_run_id=run_a, sequence_number=2))
        await producer.publish(_envelope(robot_run_id=run_b, sequence_number=3))
        await producer.flush()
        await producer.close()

    asyncio.run(_publish_interleaved())

    result_a = asyncio.run(
        run_capture(
            settings=StreamingSettings(),
            robot_id=f"robot-{run_a}",
            robot_run_id=run_a,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
            poll_timeout_seconds=2.0,
        )
    )
    assert result_a.message_count == 3
    assert result_a.first_sequence == 0
    assert result_a.last_sequence == 2
    payloads_a = _read_mcap_channel_bytes(result_a.path)
    assert len(payloads_a) == 3
    for payload in payloads_a:
        assert run_a.encode("ascii") in payload
        assert run_b.encode("ascii") not in payload

    # B's capture, run AFTER A's, on its OWN run-scoped group -- must see
    # its full 0..3 sequence, not miss b0/b1/b2 the way it used to.
    result_b = asyncio.run(
        run_capture(
            settings=StreamingSettings(),
            robot_id=f"robot-{run_b}",
            robot_run_id=run_b,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 4,
            poll_timeout_seconds=2.0,
        )
    )
    assert result_b.message_count == 4
    assert result_b.first_sequence == 0
    assert result_b.last_sequence == 3
    payloads_b = _read_mcap_channel_bytes(result_b.path)
    assert len(payloads_b) == 4
    for payload in payloads_b:
        assert run_b.encode("ascii") in payload
        assert run_a.encode("ascii") not in payload
