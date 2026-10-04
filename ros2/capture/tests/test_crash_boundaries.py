"""Phase 6.6 reliability matrix, capture crash boundaries C and D
(docs: streaming-reliability-scale-baseline.md):

  C. process dies BEFORE finalize -- no valid final MCAP, offsets never
     committed, a retry must discard the stale partial and rebuild from
     Kafka (never resume/append to it).
  D. process dies AFTER finalize but BEFORE the Kafka offset commit --
     a valid final MCAP already exists; a retry (Kafka redelivers the
     same messages, since nothing was actually committed) must not
     destructively overwrite it, and must converge (commit and return
     the existing result) rather than crash forever on the same
     already-finalized file.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sceneops_core.streaming import (  # noqa: E402
    ConsumedTelemetryEnvelope,
    EnvelopeEncoding,
    TelemetryEnvelope,
)

from capture_consumer import CAPTURE_CONSUMER_GROUP_ID, run_capture  # noqa: E402
from finalize import final_bag_path, partial_bag_path  # noqa: E402


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
    import capture_consumer as capture_consumer_module

    created: list[_FakeConsumer] = []

    def factory(*, settings, group_id, auto_offset_reset, enable_auto_commit):
        # Run-scoped (Phase 6.6.1) -- see test_capture_consumer.py's
        # identical factory for why this checks "derived from" rather
        # than exact equality.
        assert group_id.startswith(CAPTURE_CONSUMER_GROUP_ID + "-")
        assert group_id != CAPTURE_CONSUMER_GROUP_ID
        assert auto_offset_reset == "earliest"
        assert enable_auto_commit is False
        consumer = _FakeConsumer(queue=queue)
        created.append(consumer)
        return consumer

    monkeypatch.setattr(capture_consumer_module, "KafkaTelemetryConsumer", factory)
    return created


def test_boundary_c_crash_before_finalize_retry_rebuilds_from_kafka(
    tmp_path, monkeypatch
) -> None:
    """Simulates a process death mid-write: a stale, incomplete .partial
    directory is left behind with no corresponding final MCAP and no
    Kafka commit ever having happened. A fresh run_capture() attempt
    (as if Kafka simply redelivered the un-committed messages to a new
    process) must discard that stale partial and finalize cleanly."""
    robot_run_id = "run-crash-c"

    # Simulate the crash: a partial dir exists with garbage/incomplete
    # content, as if the writer had started but the process died before
    # writer.close()/validate/finalize ever ran.
    stale_partial = partial_bag_path(tmp_path, robot_run_id)
    stale_partial.mkdir(parents=True)
    (stale_partial / "garbage.tmp").write_bytes(b"incomplete, truncated bytes")

    assert not final_bag_path(tmp_path, robot_run_id).exists()

    fixtures = [_envelope(robot_run_id=robot_run_id, sequence_number=i) for i in range(3)]
    queue = [_consumed(fx, partition=0, offset=i) for i, fx in enumerate(fixtures)]
    created = _install_fake_consumer(monkeypatch, queue)

    result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id=robot_run_id,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
        )
    )

    assert result.message_count == 3
    assert result.path.exists()
    assert created[0].committed is True
    # The stale partial is gone -- replaced by the real capture, not left
    # alongside it or merged with it.
    assert not partial_bag_path(tmp_path, robot_run_id).exists()


def test_boundary_d_crash_after_finalize_before_commit_converges_on_retry(
    tmp_path, monkeypatch
) -> None:
    """First attempt finalizes successfully but its Kafka commit never
    happens (simulating the process dying in that exact window). A
    second attempt -- Kafka having redelivered the same, uncommitted
    messages to a fresh consumer -- must not crash or destructively
    overwrite the already-finalized file; it must converge: commit the
    offset now and report the existing final MCAP. The retry stamps its
    own receive time, so convergence compares the recorded messages, not
    the file bytes."""
    robot_run_id = "run-crash-d"
    fixtures = [_envelope(robot_run_id=robot_run_id, sequence_number=i) for i in range(3)]

    # Attempt 1: succeeds all the way through finalize, but we simulate
    # "died before commit" by never letting its own commit() call count
    # (the fake consumer instance is simply discarded afterward, exactly
    # as a crashed process's in-memory consumer object would be).
    queue_1 = [_consumed(fx, partition=0, offset=i) for i, fx in enumerate(fixtures)]
    _install_fake_consumer(monkeypatch, queue_1)
    first_result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id=robot_run_id,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
        )
    )
    assert first_result.path.exists()
    first_checksum = first_result.sha256

    # Attempt 2: Kafka redelivers the SAME messages (nothing was ever
    # committed) to a brand-new consumer/process.
    queue_2 = [_consumed(fx, partition=0, offset=i) for i, fx in enumerate(fixtures)]
    created_2 = _install_fake_consumer(monkeypatch, queue_2)

    second_result = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id=robot_run_id,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
        )
    )

    assert second_result.sha256 == first_checksum
    assert second_result.path == first_result.path
    assert created_2[0].committed is True  # THIS attempt's commit succeeded
    # The final file was never duplicated/corrupted -- one file, same content.
    assert second_result.path.read_bytes() == first_result.path.read_bytes()


def test_boundary_d_retry_with_different_content_fails_loudly(tmp_path, monkeypatch) -> None:
    """Conflicting retries fail: the same run id with different recorded
    messages must never converge onto the existing file or commit."""
    import pytest

    from finalize import FinalBagExistsError

    robot_run_id = "run-crash-d-conflict"
    original = [_envelope(robot_run_id=robot_run_id, sequence_number=i) for i in range(3)]
    _install_fake_consumer(
        monkeypatch, [_consumed(fx, offset=i) for i, fx in enumerate(original)]
    )
    first = asyncio.run(
        run_capture(
            settings=object(),
            robot_id="robot-1",
            robot_run_id=robot_run_id,
            output_root=tmp_path,
            stop_condition=lambda count: count >= 3,
        )
    )
    before = first.path.read_bytes()

    changed = [
        _envelope(robot_run_id=robot_run_id, sequence_number=i, payload=b"\x00\x01\x00\x00" + b"y" * 16)
        for i in range(3)
    ]
    created = _install_fake_consumer(
        monkeypatch, [_consumed(fx, offset=i) for i, fx in enumerate(changed)]
    )
    with pytest.raises(FinalBagExistsError):
        asyncio.run(
            run_capture(
                settings=object(),
                robot_id="robot-1",
                robot_run_id=robot_run_id,
                output_root=tmp_path,
                stop_condition=lambda count: count >= 3,
            )
        )

    assert created[0].committed is False
    assert first.path.read_bytes() == before
