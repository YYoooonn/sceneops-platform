"""Unit tests for capture_consumer.py: run filtering, sequence integrity,
and the full run_capture orchestration (using a fake in-process Kafka
consumer -- no real broker -- but the REAL McapCaptureWriter/finalize/
validate path, so these tests exercise real MCAP I/O).

Runs only inside the ros2 container (needs the ROS 2 interface definitions + mcap, matching
the rest of ros2/capture's flat-script import convention).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from mcap.reader import make_reader
from sceneops_core.streaming import (  # noqa: E402
    ConsumedTelemetryEnvelope,
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    build_control_envelope,
)

import capture_consumer  # noqa: E402
from capture_consumer import (  # noqa: E402
    CAPTURE_CONSUMER_GROUP_ID,
    PartitionInvariantError,
    RunEndNotObservedError,
    SequenceIntegrityError,
    _RunFilter,
    _SequenceTracker,
    run_capture,
)
from finalize import final_bag_path, partial_bag_path  # noqa: E402
from mcap_writer import UnsupportedChannelError  # noqa: E402


def _envelope(**overrides) -> TelemetryEnvelope:
    fields = dict(
        robot_id="robot-1",
        robot_run_id="run-1",
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_000_000_000,
        ingest_timestamp_ns=2_000_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=b"\x00\x01\x00\x00" + b"x" * 16,
    )
    fields.update(overrides)
    return TelemetryEnvelope(**fields)


def _consumed(envelope: TelemetryEnvelope, *, partition: int = 0, offset: int = 0):
    return ConsumedTelemetryEnvelope(
        envelope=envelope,
        key=envelope.robot_run_id.encode("utf-8"),
        topic="sceneops.robot.telemetry.v1",
        partition=partition,
        offset=offset,
    )


def _control(
    event_type: RunEventType,
    *,
    robot_id: str = "robot-1",
    robot_run_id: str = "run-1",
    sequence_number: int = 0,
    partition: int = 0,
    offset: int = 0,
    reason: str | None = None,
):
    # NOTE: build_control_envelope's payload only ever encodes `reason`
    # (e.g. b"{}" for every call that omits one) -- it does NOT encode
    # event_type, which lives in message_type instead. Two calls with
    # different event_type but no reason therefore produce BYTE-IDENTICAL
    # payloads; _SequenceTracker.accept() only ever compares payload
    # bytes, so such a pair at the same sequence_number is a harmless
    # duplicate to it, never a conflict. A test that wants a genuine
    # conflicting-duplicate at the control-sequence level must give the
    # two calls different `reason` values (or otherwise differing
    # payloads) -- same requirement any real producer would have too.
    envelope = build_control_envelope(
        event_type=event_type,
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        sequence_number=sequence_number,
        reason=reason,
    )
    return _consumed(envelope, partition=partition, offset=offset)


def _mcap_channels(path) -> set[str]:
    with open(path, "rb") as f:
        reader = make_reader(f)
        return {channel.topic for _schema, channel, _message in reader.iter_messages()}


def _bounded_stop_condition(max_calls: int = 20):
    """For tests expecting run_capture() to raise BEFORE writer.stats.
    message_count ever satisfies a normal stop_condition (e.g. a
    control-only queue, where nothing is ever written at all) -- caps
    the number of poll iterations so a wrong assumption about WHEN the
    expected exception fires produces a loud, fast assertion failure
    instead of an infinite loop polling an empty fake queue forever."""
    calls = {"n": 0}

    def _stop(count: int) -> bool:
        calls["n"] += 1
        return calls["n"] > max_calls

    return _stop


# ---------------------------------------------------------------------
# _RunFilter
# ---------------------------------------------------------------------


def test_run_filter_accepts_matching_robot_and_run() -> None:
    run_filter = _RunFilter(robot_id="robot-1", robot_run_id="run-1")
    assert run_filter.matches(_consumed(_envelope())) is True


def test_run_filter_rejects_other_robot_run_id() -> None:
    run_filter = _RunFilter(robot_id="robot-1", robot_run_id="run-1")
    other = _envelope(robot_run_id="run-OTHER")
    assert run_filter.matches(_consumed(other)) is False


def test_run_filter_rejects_other_robot_id() -> None:
    run_filter = _RunFilter(robot_id="robot-1", robot_run_id="run-1")
    other = _envelope(robot_id="robot-OTHER")
    assert run_filter.matches(_consumed(other)) is False


def test_run_filter_raises_on_partition_mismatch() -> None:
    run_filter = _RunFilter(robot_id="robot-1", robot_run_id="run-1")
    run_filter.matches(_consumed(_envelope(), partition=0))
    with pytest.raises(PartitionInvariantError):
        run_filter.matches(_consumed(_envelope(sequence_number=1), partition=1))


# ---------------------------------------------------------------------
# _SequenceTracker
# ---------------------------------------------------------------------


def test_sequence_tracker_accepts_in_order() -> None:
    tracker = _SequenceTracker()
    assert tracker.accept(sequence_number=0, payload=b"a") is True
    assert tracker.accept(sequence_number=1, payload=b"b") is True
    assert tracker.accept(sequence_number=2, payload=b"c") is True
    assert tracker.last_sequence == 2


def test_sequence_tracker_requires_first_sequence_zero() -> None:
    tracker = _SequenceTracker()
    with pytest.raises(SequenceIntegrityError):
        tracker.accept(sequence_number=1, payload=b"a")


def test_sequence_tracker_skips_exact_immediate_duplicate() -> None:
    tracker = _SequenceTracker()
    tracker.accept(sequence_number=0, payload=b"a")
    assert tracker.accept(sequence_number=0, payload=b"a") is False
    assert tracker.last_sequence == 0


def test_sequence_tracker_raises_on_conflicting_duplicate() -> None:
    tracker = _SequenceTracker()
    tracker.accept(sequence_number=0, payload=b"a")
    with pytest.raises(SequenceIntegrityError):
        tracker.accept(sequence_number=0, payload=b"DIFFERENT")


def test_sequence_tracker_raises_on_gap() -> None:
    tracker = _SequenceTracker()
    tracker.accept(sequence_number=0, payload=b"a")
    with pytest.raises(SequenceIntegrityError):
        tracker.accept(sequence_number=2, payload=b"c")


def test_sequence_tracker_raises_on_out_of_order_late() -> None:
    tracker = _SequenceTracker()
    tracker.accept(sequence_number=0, payload=b"a")
    tracker.accept(sequence_number=1, payload=b"b")
    with pytest.raises(SequenceIntegrityError):
        tracker.accept(sequence_number=0, payload=b"STALE")


# ---------------------------------------------------------------------
# run_capture end-to-end (fake Kafka consumer, real MCAP writer/finalize)
# ---------------------------------------------------------------------


class _FakeConsumer:
    def __init__(self, *, queue: list[ConsumedTelemetryEnvelope]) -> None:
        self._queue = list(queue)
        self.committed = False
        self.closed = False

    async def poll(self, timeout_seconds: float):
        if self._queue:
            return self._queue.pop(0)
        return None

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        self.closed = True


def _install_fake_consumer(monkeypatch, queue):
    created: list[_FakeConsumer] = []

    def factory(*, settings, group_id, auto_offset_reset, enable_auto_commit):
        # Run-scoped (Phase 6.6.1), never the bare base literally -- every
        # test in this file targets robot_run_id="run-1", so the derived
        # group is deterministic; this only checks it's actually DERIVED
        # (prefixed by the base), not the exact digest suffix.
        assert group_id.startswith(CAPTURE_CONSUMER_GROUP_ID + "-")
        assert group_id != CAPTURE_CONSUMER_GROUP_ID
        assert auto_offset_reset == "earliest"
        assert enable_auto_commit is False
        consumer = _FakeConsumer(queue=queue)
        created.append(consumer)
        return consumer

    monkeypatch.setattr(capture_consumer, "KafkaTelemetryConsumer", factory)
    return created


def test_run_capture_end_to_end_writes_validates_finalizes_and_commits(
    tmp_path, monkeypatch
) -> None:
    fixtures = [_envelope(sequence_number=i, payload=bytes([i]) * 8) for i in range(3)]
    queue = [_consumed(fx, partition=0, offset=100 + i) for i, fx in enumerate(fixtures)]
    created = _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= len(fixtures),
        )
    )

    assert result.message_count == 3
    assert result.first_sequence == 0
    assert result.last_sequence == 2
    assert result.partition == 0
    assert result.first_offset == 100
    assert result.last_offset == 102
    assert result.path == final_bag_path(tmp_path, "run-1") / "run-1_0.mcap"
    assert result.path.exists()
    assert len(result.sha256) == 64

    assert created[0].committed is True
    assert created[0].closed is True
    assert not partial_bag_path(tmp_path, "run-1").exists()


def test_run_capture_ignores_messages_for_other_runs(tmp_path, monkeypatch) -> None:
    target = [_envelope(sequence_number=i) for i in range(2)]
    other = _envelope(robot_run_id="run-OTHER", sequence_number=0)
    queue = [
        _consumed(other, partition=0, offset=0),
        _consumed(target[0], partition=0, offset=1),
        _consumed(target[1], partition=0, offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 2,
        )
    )

    assert result.message_count == 2
    assert result.first_offset == 1
    assert result.last_offset == 2


def test_run_capture_commits_only_after_finalize(tmp_path, monkeypatch) -> None:
    """Durability-ordering test: fails if run_capture is ever changed to
    commit Kafka offsets before (or without) finalizing the MCAP bag."""
    fixtures = [_envelope(sequence_number=0)]
    queue = [_consumed(fixtures[0], partition=0, offset=0)]
    created = _install_fake_consumer(monkeypatch, queue)

    call_order: list[str] = []
    real_finalize_bag = capture_consumer.finalize_bag

    def spy_finalize_bag(output_root, robot_run_id):
        call_order.append("finalize")
        return real_finalize_bag(output_root, robot_run_id)

    monkeypatch.setattr(capture_consumer, "finalize_bag", spy_finalize_bag)

    orig_commit = _FakeConsumer.commit

    async def spy_commit(self):
        call_order.append("commit")
        return await orig_commit(self)

    monkeypatch.setattr(_FakeConsumer, "commit", spy_commit)

    asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 1,
        )
    )

    assert call_order == ["finalize", "commit"]
    assert created[0].committed is True


def test_run_capture_raises_on_partition_invariant_violation_and_does_not_commit(
    tmp_path, monkeypatch
) -> None:
    fixtures = [_envelope(sequence_number=0), _envelope(sequence_number=1)]
    queue = [
        _consumed(fixtures[0], partition=0, offset=0),
        _consumed(fixtures[1], partition=1, offset=1),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    with pytest.raises(PartitionInvariantError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=lambda count: count >= 2,
            )
        )

    assert created[0].committed is False
    assert not final_bag_path(tmp_path, "run-1").exists()


def test_run_capture_raises_on_sequence_gap_and_does_not_finalize_or_commit(
    tmp_path, monkeypatch
) -> None:
    fixtures = [_envelope(sequence_number=0), _envelope(sequence_number=2)]
    queue = [
        _consumed(fixtures[0], partition=0, offset=0),
        _consumed(fixtures[1], partition=0, offset=1),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    with pytest.raises(SequenceIntegrityError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=lambda count: count >= 2,
            )
        )

    assert created[0].committed is False
    assert not final_bag_path(tmp_path, "run-1").exists()


def test_run_capture_raises_on_unsupported_channel_and_does_not_finalize_or_commit(
    tmp_path, monkeypatch
) -> None:
    bad = _envelope(channel="/not/registered", message_type="std_msgs/msg/String")
    queue = [_consumed(bad, partition=0, offset=0)]
    created = _install_fake_consumer(monkeypatch, queue)

    with pytest.raises(UnsupportedChannelError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=lambda count: count >= 1,
            )
        )

    assert created[0].committed is False
    assert not final_bag_path(tmp_path, "run-1").exists()


# ---------------------------------------------------------------------
# Lifecycle control envelopes (Phase 7.2.1)
# ---------------------------------------------------------------------


def test_run_capture_handles_run_start_telemetry_run_end(tmp_path, monkeypatch) -> None:
    telemetry = [_envelope(sequence_number=i) for i in range(2)]
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        _consumed(telemetry[0], offset=1),
        _consumed(telemetry[1], offset=2),
        _control(RunEventType.RUN_END, sequence_number=1, offset=3),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            # Control events never count toward writer.stats.message_count
            # -- stop_condition only ever sees telemetry, unchanged.
            stop_condition=lambda count: count >= 2,
        )
    )

    assert result.message_count == 2
    assert result.first_sequence == 0
    assert result.last_sequence == 1
    # Kafka provenance (first/last offset) reflects every record this
    # capture actually consumed, control events included -- first_offset
    # is the RUN_START control event's own offset (0), not the first
    # telemetry message's. last_offset stops at 2 (the second telemetry
    # message), NOT the RUN_END at offset 3 -- stop_condition is checked
    # BEFORE each poll and is already satisfied (writer.stats.
    # message_count == 2) once the second telemetry message is written,
    # so RUN_END is correctly never consumed in THIS test's configuration
    # (control events interleaved with the stop boundary are covered
    # separately by the real-Kafka lifecycle integration test, which
    # consumes RUN_END too).
    assert result.first_offset == 0
    assert result.last_offset == 2
    assert created[0].committed is True
    assert _mcap_channels(result.path) == {"/vehicle/odom"}


def test_run_capture_mcap_contains_no_control_channel_records(tmp_path, monkeypatch) -> None:
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        _consumed(_envelope(sequence_number=0), offset=1),
        _control(RunEventType.RUN_END, sequence_number=1, offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 1,
        )
    )

    assert "/session/control" not in _mcap_channels(result.path)
    assert result.message_count == 1  # only the one real telemetry record


def test_run_capture_control_events_interleaved_with_telemetry(tmp_path, monkeypatch) -> None:
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        _consumed(_envelope(sequence_number=0), offset=1),
        _consumed(_envelope(sequence_number=1), offset=2),
        _consumed(_envelope(sequence_number=2), offset=3),
        _control(RunEventType.RUN_END, sequence_number=1, offset=4),
    ]
    _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
        )
    )

    assert result.message_count == 3
    assert result.first_sequence == 0
    assert result.last_sequence == 2


def test_run_capture_detects_gap_in_control_event_sequence(tmp_path, monkeypatch) -> None:
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        _consumed(_envelope(sequence_number=0), offset=1),
        # Control stream jumps straight to sequence 5 -- a gap in the
        # control space specifically; telemetry's own sequence is fine.
        _control(RunEventType.RUN_END, sequence_number=5, offset=2),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    with pytest.raises(SequenceIntegrityError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=_bounded_stop_condition(),
            )
        )

    assert created[0].committed is False
    assert not final_bag_path(tmp_path, "run-1").exists()


def test_run_capture_duplicate_run_start_is_skipped_telemetry_unaffected(
    tmp_path, monkeypatch
) -> None:
    # Exact immediate redelivery of RUN_START (same sequence, same
    # payload) -- silently skipped, same policy telemetry already has.
    run_start = build_control_envelope(
        event_type=RunEventType.RUN_START,
        robot_id="robot-1",
        robot_run_id="run-1",
        sequence_number=0,
    )
    queue = [
        _consumed(run_start, offset=0),
        _consumed(run_start, offset=1),  # exact duplicate redelivery
        _consumed(_envelope(sequence_number=0), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 1,
        )
    )
    assert result.message_count == 1  # duplicate RUN_START never affected telemetry


def test_run_capture_detects_conflicting_duplicate_control_event(tmp_path, monkeypatch) -> None:
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0, reason="first"),
        # Same control sequence number as above, genuinely different
        # payload bytes (different `reason`) -- a real conflict, never
        # silently resolved. (Two DIFFERENT event_types at the same
        # sequence_number with no reason would NOT trigger this --
        # see _control's own docstring note above.)
        _control(RunEventType.RUN_START, sequence_number=0, offset=1, reason="second"),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    with pytest.raises(SequenceIntegrityError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=_bounded_stop_condition(),
            )
        )

    assert created[0].committed is False
    assert not final_bag_path(tmp_path, "run-1").exists()


def test_run_capture_stop_on_run_end_finalizes_exactly_at_run_end(tmp_path, monkeypatch) -> None:
    """The normal end of a streamed run: no message count, no idle
    timeout -- RUN_END alone ends the capture, after every record that
    preceded it, and the recording carries the per-channel counts."""
    telemetry = [
        _envelope(sequence_number=0),
        _envelope(
            sequence_number=1, channel="/vehicle/imu", message_type="sensor_msgs/msg/Imu"
        ),
        _envelope(sequence_number=2),
    ]
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        *[_consumed(fx, offset=1 + i) for i, fx in enumerate(telemetry)],
        _control(RunEventType.RUN_END, sequence_number=1, offset=4),
        # Anything after RUN_END is outside this capture.
        _consumed(_envelope(sequence_number=3), offset=5),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=_bounded_stop_condition(50),
            stop_on_run_end=True,
        )
    )

    assert result.message_count == 3
    assert result.last_offset == 4
    assert result.per_channel_counts == {"/vehicle/odom": 2, "/vehicle/imu": 1}
    assert created[0].committed is True


def test_run_capture_stopped_before_run_end_is_not_finalized(tmp_path, monkeypatch) -> None:
    """With stop_on_run_end the stop condition is only an abort guard: a
    run whose RUN_END never arrived (a killed bridge, a truncated stream)
    must not become a finalized capture that publishes as a complete
    RobotRun. Nothing is finalized, nothing is committed, and the partial
    bag stays for the next attempt to discard."""
    queue = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        _consumed(_envelope(sequence_number=0), offset=1),
        _consumed(_envelope(sequence_number=1), offset=2),
    ]
    created = _install_fake_consumer(monkeypatch, queue)

    with pytest.raises(RunEndNotObservedError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=_bounded_stop_condition(3),
                stop_on_run_end=True,
            )
        )

    assert created[0].committed is False
    assert created[0].closed is True
    assert not final_bag_path(tmp_path, "run-1").exists()
    assert partial_bag_path(tmp_path, "run-1").exists()


def test_run_capture_retry_after_unfinished_attempt_finalizes_on_run_end(
    tmp_path, monkeypatch
) -> None:
    """The aborted attempt leaves only a partial bag; a later attempt that
    does see RUN_END discards it and finalizes from Kafka."""
    truncated = [
        _control(RunEventType.RUN_START, sequence_number=0, offset=0),
        _consumed(_envelope(sequence_number=0), offset=1),
    ]
    _install_fake_consumer(monkeypatch, truncated)
    with pytest.raises(RunEndNotObservedError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id="run-1",
                output_root=tmp_path,
                stop_condition=_bounded_stop_condition(2),
                stop_on_run_end=True,
            )
        )

    complete = [
        *truncated,
        _consumed(_envelope(sequence_number=1), offset=2),
        _control(RunEventType.RUN_END, sequence_number=1, offset=3),
    ]
    created = _install_fake_consumer(monkeypatch, complete)
    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=_bounded_stop_condition(50),
            stop_on_run_end=True,
        )
    )

    assert result.message_count == 2
    assert created[0].committed is True
    assert final_bag_path(tmp_path, "run-1").exists()
    assert not partial_bag_path(tmp_path, "run-1").exists()


def test_run_capture_records_receive_time_and_transport_sequence(tmp_path, monkeypatch) -> None:
    import time

    fixtures = [
        _envelope(sequence_number=i, source_timestamp_ns=5_000 + i, ingest_timestamp_ns=9_000 + i)
        for i in range(3)
    ]
    _install_fake_consumer(
        monkeypatch, [_consumed(fx, offset=i) for i, fx in enumerate(fixtures)]
    )
    before = time.time_ns()

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
        )
    )
    after = time.time_ns()

    with open(result.path, "rb") as f:
        messages = [m for _s, _c, m in make_reader(f).iter_messages()]
    assert [m.sequence for m in messages] == [1, 2, 3]
    assert [m.publish_time for m in messages] == [9_000, 9_001, 9_002]
    assert all(before <= m.log_time <= after for m in messages)
    assert [m.log_time for m in messages] == sorted(m.log_time for m in messages)


def test_run_capture_writes_sensor_channels_from_a_channel_file(tmp_path, monkeypatch) -> None:
    from sceneops_core.streaming import build_channel_registry

    channels = Path(__file__).resolve().parents[2] / "channels" / "surround-camera-lidar.json"
    registry = build_channel_registry([channels])
    lidar_payload = bytes(range(256)) * 2800  # ~700 kB, a realistic lidar sweep
    fixtures = [
        _envelope(
            sequence_number=0,
            channel="/lidar/top/points",
            message_type="sensor_msgs/msg/PointCloud2",
            payload=lidar_payload,
        ),
        _envelope(
            sequence_number=1,
            channel="/camera/front/camera_info",
            message_type="sensor_msgs/msg/CameraInfo",
        ),
    ]
    _install_fake_consumer(
        monkeypatch, [_consumed(fx, offset=i) for i, fx in enumerate(fixtures)]
    )

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id="run-1",
            output_root=tmp_path,
            stop_condition=lambda count: count >= 2,
            registry=registry,
        )
    )

    assert _mcap_channels(result.path) == {"/lidar/top/points", "/camera/front/camera_info"}
    with open(result.path, "rb") as f:
        first = next(make_reader(f).iter_messages())[2]
    assert first.data == lidar_payload
