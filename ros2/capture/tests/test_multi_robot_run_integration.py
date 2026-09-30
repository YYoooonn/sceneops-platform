"""Phase 6.6 reliability matrix item 5 (multi-RobotRun isolation),
against the REAL local Kafka broker -- no monkeypatched consumer, unlike
the rest of this test package.

Two things are tested and reported separately, because they have
different outcomes:

  1. Within ONE capture invocation, RunFilter correctly isolates the
     target robot_run_id from interleaved messages belonging to a
     DIFFERENT robot_run_id on the same topic/partition -- the resulting
     MCAP contains only the target run's messages. This is the
     supported, tested guarantee.

  2. Two SEQUENTIAL, independent capture invocations (run A's capture,
     then run B's, both using the frozen shared
     CAPTURE_CONSUMER_GROUP_ID -- ros2/capture has no per-robot_run_id
     group scheme) do NOT get fully independent offset cursors: if A's
     own poll loop has to read past some of B's interleaved messages to
     reach A's own target count, A's commit() advances the shared
     group's committed offset past those B messages too, even though A
     never wrote them anywhere. A later, separate capture for B can then
     miss its own early messages. This is a real, documented v1 limit,
     not silently fixed here (see
     docs/architecture/streaming-transport.md's Part 3 reliability
     section) -- the local dev topic has exactly one partition
     (`sceneops.robot.telemetry.v1`, `PartitionCount: 1`), which makes
     this the realistic default, not a rare edge case.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcap.reader import make_reader  # noqa: E402
from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope  # noqa: E402
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings  # noqa: E402

import pytest  # noqa: E402

from capture_consumer import SequenceIntegrityError, run_capture  # noqa: E402


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
        payload=bytes([0x00, 0x01, 0x00, 0x00]) + robot_run_id.encode("ascii").ljust(16, b"\x00"),
    )


def _read_mcap_channel_bytes(path) -> list[bytes]:
    with open(path, "rb") as f:
        reader = make_reader(f)
        return [message.data for _s, _c, message in reader.iter_messages()]


def test_run_filter_isolates_target_run_from_interleaved_other_run(tmp_path) -> None:
    """Within one capture invocation: A's messages interleaved with B's on
    the same real topic/partition -- A's finalized MCAP must contain ONLY
    A's messages, never B's, regardless of interleaving order."""
    import uuid

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


def test_sequential_captures_sharing_group_do_not_fully_isolate_offsets(tmp_path) -> None:
    """Documents (does not "fix") a real v1 limit: A's capture polling
    past B's interleaved messages to reach A's own target advances the
    SHARED consumer group's committed offset past those B messages too.
    A's own MCAP is still correctly A-only (RunFilter); the effect shows
    up in B's LATER, separate capture attempt -- which does not silently
    return truncated data, because the missing prefix also violates the
    frozen "first sequence must be 0" invariant (Phase 6.3), so B's
    capture fails loudly (SequenceIntegrityError) instead."""
    import uuid

    invocation = uuid.uuid4().hex[:10]
    run_a = f"seq-a-{invocation}"
    run_b = f"seq-b-{invocation}"

    async def _publish_interleaved():
        settings = StreamingSettings()
        producer = KafkaTelemetryProducer(settings=settings)
        # a0, b0, a1, b1, a2, b2, b3 -- A only needs 3 (a0,a1,a2); B has 4
        # (b0..b3), but b2 sits BEFORE a2 in publish/offset order.
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

    # A's capture: needs only 3 messages, reaches them at Kafka offset 5
    # (0-indexed: a0=0,b0=1,a1=2,b1=3,b2=4,a2=5) -- committing offset 5
    # silently carries B's b0/b1/b2 "past" as far as the shared group's
    # cursor is concerned, even though A wrote none of them.
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
    assert result_a.message_count == 3  # A's own MCAP is still correct

    # B's capture, run AFTER A's, sharing the same frozen consumer group:
    # only b3 (offset 6) remains after the group's committed position --
    # b0/b1/b2 are gone as far as this group is concerned. The tracker's
    # own "first sequence must be 0" check catches this and fails loudly
    # rather than silently starting B's MCAP from sequence 3.
    with pytest.raises(SequenceIntegrityError, match="expected 0"):
        asyncio.run(
            run_capture(
                settings=StreamingSettings(),
                robot_id=f"robot-{run_b}",
                robot_run_id=run_b,
                output_root=tmp_path,
                stop_condition=lambda count: count >= 1,
                poll_timeout_seconds=2.0,
            )
        )
