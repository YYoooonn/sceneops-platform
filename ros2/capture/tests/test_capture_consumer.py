"""Unit tests for capture_consumer.py: run filtering, sequence integrity,
and the full run_capture orchestration (using a fake in-process Kafka
consumer -- no real broker -- but the REAL McapCaptureWriter/finalize/
validate path, so these tests exercise real MCAP I/O).

Runs only inside the ros2 container (needs rosbag2_py + mcap, matching
the rest of ros2/capture's flat-script import convention).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sceneops_core.streaming import (  # noqa: E402
    ConsumedTelemetryEnvelope,
    EnvelopeEncoding,
    TelemetryEnvelope,
)

import capture_consumer  # noqa: E402
from capture_consumer import (  # noqa: E402
    CAPTURE_CONSUMER_GROUP_ID,
    PartitionInvariantError,
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
