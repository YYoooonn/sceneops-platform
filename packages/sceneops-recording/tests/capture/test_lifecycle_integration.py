"""``run_capture`` against REAL Kafka with the run lifecycle control events enabled --
no monkeypatched consumer, matching test_multi_robot_run_integration.py's own
convention.

A RUN_START/telemetry/RUN_END stream (exactly what a lifecycle-events-enabled
StreamingBridgeNode publishes) is captured successfully, producing a valid MCAP with
no control-channel records. The bridge side of the same invariant (the real bridge
publishes RUN_START, its telemetry and RUN_END, and the receipt spans them) is
integrations/ros2-kafka-bridge/tests/test_bridge_run_lifecycle_capture.py.

Runs only inside the ROS 2 capture image (``make streaming-test``) and needs a running
Kafka broker (``make streaming-up``).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from group_state import assert_leaves_no_group_state
from mcap.reader import make_reader

from sceneops_recording.capture.consumer import (
    RunEndNotObservedError,
    SequenceIntegrityError,
    run_capture,
)
from sceneops_recording.capture.finalize import final_bag_path
from sceneops_recording.capture.lifecycle import RunIncompleteError, RunLifecycleError
from sceneops_recording.capture.validation import McapValidationError
from sceneops_recording.capture_receipt import FinalizationReason
from sceneops_recording.capture.receipt_io import read_capture_receipt
from sceneops_streaming import (
    EnvelopeEncoding,
    KafkaTelemetryProducer,
    RunEventType,
    StreamingSettings,
    TelemetryEnvelope,
    build_control_envelope,
)


def _unique_test_topic() -> str:
    # A fresh, disposable, per-invocation topic so this test never has to
    # scan unrelated accumulated history from other local/CI runs.
    return f"sceneops.robot.telemetry.lifecycle-capture-test.{uuid.uuid4().hex[:12]}.v1"


def _envelope(
    *, robot_run_id: str, robot_id: str, sequence_number: int
) -> TelemetryEnvelope:
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
                event_type=RunEventType.RUN_START,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
            )
        )
        for seq in range(telemetry_count):
            await producer.publish(
                _envelope(
                    robot_run_id=robot_run_id, robot_id=robot_id, sequence_number=seq
                )
            )
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                sequence_number=1,
                telemetry_count=telemetry_count,
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
            # Count-bounded stop (no stop_on_run_end): the telemetry-only
            # count -- control events never count toward
            # writer.stats.message_count.
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

        async def end(run_id, telemetry_count):
            await producer.publish(
                build_control_envelope(
                    event_type=RunEventType.RUN_END,
                    robot_id=f"robot-{run_id}",
                    robot_run_id=run_id,
                    sequence_number=1,
                    telemetry_count=telemetry_count,
                )
            )

        async def telem(run_id, seq):
            await producer.publish(
                _envelope(
                    robot_run_id=run_id, robot_id=f"robot-{run_id}", sequence_number=seq
                )
            )

        await start(target_id)
        await start(other_id)
        await telem(target_id, 0)
        await telem(other_id, 0)
        await telem(other_id, 1)
        await telem(target_id, 1)
        await end(target_id, 2)
        await telem(other_id, 2)
        await end(other_id, 3)
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


# ---------------------------------------------------------------------
# Lifecycle-governed capture on a real broker
# ---------------------------------------------------------------------


async def _publish_session(
    topic: str,
    robot_id: str,
    run_id: str,
    *,
    telemetry: int,
    run_start: bool = True,
    declared: int | None = None,
    sequences: list[int] | None = None,
    run_end: bool = True,
    run_end_payload: bytes | None = None,
    extra_run_ends: int = 0,
) -> None:
    """One session as a bridge publishes it. The keyword arguments inject the
    faults of the completeness tests: ``declared`` (the count RUN_END states, by
    default the telemetry published), ``sequences`` (the exact telemetry
    sequence numbers published, to drop, repeat or reorder records),
    ``run_end=False`` (a producer that died first), ``run_end_payload`` (a
    RUN_END whose payload is replaced, e.g. with no count) and
    ``extra_run_ends`` (redelivered RUN_ENDs)."""
    producer = KafkaTelemetryProducer(settings=StreamingSettings(telemetry_topic=topic))
    if run_start:
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_START,
                robot_id=robot_id,
                robot_run_id=run_id,
            )
        )
    for seq in sequences if sequences is not None else range(telemetry):
        await producer.publish(
            _envelope(robot_run_id=run_id, robot_id=robot_id, sequence_number=seq)
        )
    if run_end:
        run_end_envelope = build_control_envelope(
            event_type=RunEventType.RUN_END,
            robot_id=robot_id,
            robot_run_id=run_id,
            sequence_number=1,
            telemetry_count=telemetry if declared is None else declared,
        )
        if run_end_payload is not None:
            run_end_envelope = run_end_envelope.model_copy(
                update={"payload": run_end_payload}
            )
        for _ in range(1 + extra_run_ends):
            await producer.publish(run_end_envelope)
    await producer.flush()
    await producer.close()


def _governed_capture(topic: str, robot_id: str, run_id: str, output_root):
    return run_capture(
        settings=StreamingSettings(telemetry_topic=topic),
        robot_id=robot_id,
        robot_run_id=run_id,
        output_root=output_root,
        stop_on_run_end=True,
        # Covers the scan of the other run's history.
        idle_timeout_seconds=30,
        poll_timeout_seconds=1.0,
    )


def test_governed_capture_after_other_runs_history_finalizes_without_group_state(
    tmp_path,
) -> None:
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-governed-{invocation}"
    topic = _unique_test_topic()

    async def publish() -> None:
        await _publish_session(
            topic, "robot-other", f"other-{invocation}", telemetry=200
        )
        await _publish_session(topic, robot_id, run_id, telemetry=5)

    asyncio.run(publish())

    result = asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))

    assert result.message_count == 5
    # RUN_START .. RUN_END are consecutive on the run's partition.
    assert result.last_offset - result.first_offset + 1 == 5 + 2
    receipt = read_capture_receipt(final_bag_path(tmp_path, run_id))
    assert receipt.finalization.reason is FinalizationReason.EXPLICIT_RUN_END
    assert_leaves_no_group_state(topic, result.partition, run_id)


def test_run_without_run_start_is_not_finalized(tmp_path) -> None:
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-no-start-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(_publish_session(topic, robot_id, run_id, telemetry=5, run_start=False))

    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))

    assert not final_bag_path(tmp_path, run_id).exists()


def test_rerun_of_a_finalized_run_converges(tmp_path) -> None:
    """The finalized bag marks the run captured, not any Kafka state: every
    attempt reads the run again from its start, meets the finalized bag, and
    converges onto it."""
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-rerun-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(_publish_session(topic, robot_id, run_id, telemetry=5))

    first = asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))
    second = asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))

    assert second.path == first.path
    assert second.sha256 == first.sha256
    assert (second.first_offset, second.last_offset) == (
        first.first_offset,
        first.last_offset,
    )
    assert_leaves_no_group_state(topic, first.partition, run_id)


# ---------------------------------------------------------------------
# Completeness: an incomplete stream never finalizes (real broker)
# ---------------------------------------------------------------------


def _assert_not_finalized(tmp_path, run_id) -> None:
    assert not final_bag_path(tmp_path, run_id).exists()


def _run_incomplete(tmp_path, label, error, match, **session):
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-{label}-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(_publish_session(topic, robot_id, run_id, **session))

    with pytest.raises(error, match=match):
        asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))

    _assert_not_finalized(tmp_path, run_id)


def test_missing_tail_telemetry_is_not_finalized(tmp_path) -> None:
    """RUN_START, telemetry 0..4, [5..9 lost], RUN_END(10): every record that
    arrived is in order, so only the declared count exposes the loss."""
    _run_incomplete(
        tmp_path,
        "tail",
        RunIncompleteError,
        "declares 10 telemetry record.*observed 5",
        telemetry=5,
        declared=10,
    )


def test_missing_middle_telemetry_is_not_finalized(tmp_path) -> None:
    _run_incomplete(
        tmp_path,
        "middle",
        SequenceIntegrityError,
        "sequence gap",
        telemetry=0,
        sequences=[0, 1, 2, 5, 6],
        declared=7,
    )


def test_reordered_telemetry_is_not_finalized(tmp_path) -> None:
    _run_incomplete(
        tmp_path,
        "reordered",
        SequenceIntegrityError,
        "sequence gap",
        telemetry=0,
        sequences=[0, 2, 1, 3],
        declared=4,
    )


def test_run_end_declaring_fewer_than_observed_is_not_finalized(tmp_path) -> None:
    _run_incomplete(
        tmp_path,
        "overcount",
        RunIncompleteError,
        "more than the producer assigned",
        telemetry=5,
        declared=3,
    )


def test_run_end_without_a_count_is_not_finalized(tmp_path) -> None:
    _run_incomplete(
        tmp_path,
        "nocount",
        RunLifecycleError,
        "telemetry_count",
        telemetry=5,
        run_end_payload=b"{}",
    )


def test_zero_telemetry_run_is_complete_but_has_no_recording(tmp_path) -> None:
    """RUN_START, RUN_END(0): the count matches, so completeness holds, but a
    recording of no messages is still refused (McapValidationError)."""
    _run_incomplete(
        tmp_path, "empty", McapValidationError, "zero messages", telemetry=0
    )


def test_zero_telemetry_run_declaring_telemetry_is_incomplete(tmp_path) -> None:
    _run_incomplete(
        tmp_path,
        "emptylie",
        RunIncompleteError,
        "declares 4 telemetry record.*observed 0",
        telemetry=0,
        declared=4,
    )


def test_duplicate_telemetry_does_not_change_the_count(tmp_path) -> None:
    """A producer-level redelivery of the previous record is skipped; the run
    still has exactly the declared number of distinct sequence numbers."""
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-dup-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(
        _publish_session(
            topic, robot_id, run_id, telemetry=0, sequences=[0, 1, 1, 2], declared=3
        )
    )

    result = asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))

    assert result.message_count == 3
    assert (result.first_sequence, result.last_sequence) == (0, 2)


def test_duplicate_run_end_is_not_read_after_the_first(tmp_path) -> None:
    """Capture ends at the first RUN_END; a redelivered one stays on the topic
    unread."""
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-dupend-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(
        _publish_session(topic, robot_id, run_id, telemetry=3, extra_run_ends=1)
    )

    result = asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))

    assert result.message_count == 3
    assert result.last_offset - result.first_offset + 1 == 3 + 2
    assert_leaves_no_group_state(topic, result.partition, run_id)


def test_producer_that_died_before_run_end_is_not_finalized(tmp_path) -> None:
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-died-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(_publish_session(topic, robot_id, run_id, telemetry=5, run_end=False))

    with pytest.raises(RunEndNotObservedError):
        asyncio.run(
            run_capture(
                settings=StreamingSettings(telemetry_topic=topic),
                robot_id=robot_id,
                robot_run_id=run_id,
                output_root=tmp_path,
                stop_on_run_end=True,
                idle_timeout_seconds=10,
                poll_timeout_seconds=1.0,
            )
        )

    _assert_not_finalized(tmp_path, run_id)


def test_replay_after_a_rejected_attempt_is_rejected_again(tmp_path) -> None:
    """The verdict is a function of the log: a capture restarted after a
    rejected attempt reads the same records and rejects them the same way."""
    invocation = uuid.uuid4().hex[:10]
    robot_id, run_id = f"robot-{invocation}", f"lifecycle-replay-{invocation}"
    topic = _unique_test_topic()
    asyncio.run(_publish_session(topic, robot_id, run_id, telemetry=5, declared=9))

    for _ in range(2):
        with pytest.raises(RunIncompleteError):
            asyncio.run(_governed_capture(topic, robot_id, run_id, tmp_path))
        _assert_not_finalized(tmp_path, run_id)
