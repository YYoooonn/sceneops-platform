"""Unit tests for router.py's ContinuousCaptureRouter: interleaved
multi-RobotRun routing, per-run sequence/partition isolation, per-run
failure isolation, resource bounds, finalization, and the Kafka
offset-commit-safety policy -- using a fake in-process Kafka consumer
(no real broker) but the REAL McapCaptureWriter/finalize/validate path,
matching test_capture_consumer.py's own convention.

Runs only inside the ros2 container (needs rosbag2_py + mcap).
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
    TelemetryEnvelope,
)
from sceneops_streaming.errors import EnvelopeDecodeError  # noqa: E402

import router as router_module  # noqa: E402
from finalize import final_bag_path, partial_bag_path  # noqa: E402
from router import (  # noqa: E402
    ContinuousCaptureRouter,
    MaxActiveRunsExceededError,
    ROUTER_CONSUMER_GROUP_ID,
    RunIdentityConflictError,
    UnknownRunError,
)


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
        payload=bytes([0x00, 0x01, 0x00, 0x00]) + b"x" * 16,
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


def _read_mcap_messages(path):
    """[(channel, log_time, publish_time, payload), ...] from a
    finalized MCAP file, read back the same way validation.py/
    RosbagAdapter do -- never trust in-memory writer counters."""
    out = []
    with open(path, "rb") as f:
        reader = make_reader(f)
        for _schema, channel, message in reader.iter_messages():
            out.append((channel.topic, message.log_time, message.publish_time, message.data))
    return out


class _FakeConsumer:
    def __init__(self, *, queue: list[ConsumedTelemetryEnvelope]) -> None:
        self._queue = list(queue)
        self.commit_offsets_calls: list[dict[int, int]] = []
        self.closed = False

    async def poll(self, timeout_seconds: float):
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return None

    async def commit_offsets(self, offsets: dict[int, int]) -> None:
        self.commit_offsets_calls.append(dict(offsets))

    async def close(self) -> None:
        self.closed = True


def _install_fake_consumer(monkeypatch, queue):
    created: list[_FakeConsumer] = []

    def factory(*, settings, group_id, auto_offset_reset, enable_auto_commit):
        assert group_id == ROUTER_CONSUMER_GROUP_ID
        assert auto_offset_reset == "earliest"
        assert enable_auto_commit is False
        consumer = _FakeConsumer(queue=queue)
        created.append(consumer)
        return consumer

    monkeypatch.setattr(router_module, "KafkaTelemetryConsumer", factory)
    return created


def _make_router(tmp_path, *, max_active_runs: int = 64) -> ContinuousCaptureRouter:
    return ContinuousCaptureRouter(
        settings=object(), output_root=tmp_path, max_active_runs=max_active_runs
    )


# ---------------------------------------------------------------------
# Two heavily interleaved RobotRuns
# ---------------------------------------------------------------------


def test_two_interleaved_runs_each_produce_only_their_own_messages(tmp_path, monkeypatch) -> None:
    queue = []
    for i in range(5):
        queue.append(_consumed(_envelope(robot_run_id="run-A", sequence_number=i), offset=2 * i))
        queue.append(_consumed(_envelope(robot_run_id="run-B", sequence_number=i), offset=2 * i + 1))
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=10, idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    assert results["run-A"].message_count == 5
    assert results["run-B"].message_count == 5
    assert results["run-A"].first_sequence == 0
    assert results["run-A"].last_sequence == 4
    assert results["run-B"].first_sequence == 0
    assert results["run-B"].last_sequence == 4


def test_no_cross_run_mcap_records_when_heavily_interleaved(tmp_path, monkeypatch) -> None:
    queue = []
    for i in range(8):
        queue.append(
            _consumed(
                _envelope(robot_run_id="run-A", sequence_number=i, payload=bytes([0xA0 + i])),
                offset=2 * i,
            )
        )
        queue.append(
            _consumed(
                _envelope(robot_run_id="run-B", sequence_number=i, payload=bytes([0xB0 + i])),
                offset=2 * i + 1,
            )
        )
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=16, idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    a_messages = _read_mcap_messages(results["run-A"].path)
    b_messages = _read_mcap_messages(results["run-B"].path)

    assert len(a_messages) == 8
    assert len(b_messages) == 8
    a_payloads = {data for _, _, _, data in a_messages}
    b_payloads = {data for _, _, _, data in b_messages}
    assert a_payloads == {bytes([0xA0 + i]) for i in range(8)}
    assert b_payloads == {bytes([0xB0 + i]) for i in range(8)}
    assert a_payloads.isdisjoint(b_payloads)


def test_byte_exact_payload_and_timestamp_mapping_preserved(tmp_path, monkeypatch) -> None:
    envelope = _envelope(
        robot_run_id="run-A",
        source_timestamp_ns=1_700_000_000_123_000_000,
        ingest_timestamp_ns=1_700_000_000_456_000_000,
        payload=bytes(range(256)),
    )
    queue = [_consumed(envelope, offset=0)]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=1, idle_timeout_seconds=0.1)
        return await r.finalize_run("run-A")

    result = asyncio.run(scenario())
    [(channel, log_time, publish_time, payload)] = _read_mcap_messages(result.path)

    assert channel == "/vehicle/odom"
    assert payload == bytes(range(256))
    # Frozen mapping (unchanged from mcap_writer.py): log_time =
    # source_timestamp_ns, publish_time = ingest_timestamp_ns.
    assert log_time == 1_700_000_000_123_000_000
    assert publish_time == 1_700_000_000_456_000_000


# ---------------------------------------------------------------------
# Many interleaved RobotRuns
# ---------------------------------------------------------------------


def test_many_interleaved_runs_all_independently_correct(tmp_path, monkeypatch) -> None:
    num_runs = 12
    per_run = 6
    queue = []
    offset = 0
    for seq in range(per_run):
        for run_idx in range(num_runs):
            queue.append(
                _consumed(
                    _envelope(robot_run_id=f"run-{run_idx}", sequence_number=seq),
                    offset=offset,
                )
            )
            offset += 1
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, max_active_runs=num_runs)

    async def scenario():
        await r.run_for(max_messages=num_runs * per_run, idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    assert len(results) == num_runs
    for run_idx in range(num_runs):
        result = results[f"run-{run_idx}"]
        assert result.message_count == per_run
        assert result.first_sequence == 0
        assert result.last_sequence == per_run - 1


# ---------------------------------------------------------------------
# Independent gap / duplicate / conflicting-duplicate handling
# ---------------------------------------------------------------------


def test_independent_gap_detection_only_affects_the_gapped_run(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        # run-A jumps straight to sequence 5 -- a gap, run-A only.
        _consumed(_envelope(robot_run_id="run-A", sequence_number=5), offset=2),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1), offset=3),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=2), offset=4),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=5, idle_timeout_seconds=0.1))

    assert "run-A" in r.failed_runs
    assert "SequenceIntegrityError" in r.failed_runs["run-A"]
    assert "run-B" not in r.failed_runs
    assert r.active_run_states()["run-B"].message_count == 3


def test_independent_duplicate_handling_per_run(tmp_path, monkeypatch) -> None:
    payload_a = bytes([0xAA])
    payload_b = bytes([0xBB])
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=payload_a), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0, payload=payload_b), offset=1),
        # Exact immediate redelivery for BOTH runs -- both should silently skip.
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=payload_a), offset=2),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0, payload=payload_b), offset=3),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=1, payload=payload_a), offset=4),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=5, idle_timeout_seconds=0.1))

    assert r.stats.messages_skipped_duplicate == 2
    assert r.active_run_states()["run-A"].message_count == 2
    assert r.active_run_states()["run-B"].message_count == 1
    assert "run-A" not in r.failed_runs
    assert "run-B" not in r.failed_runs


def test_conflicting_duplicate_rejected_and_isolated_to_its_own_run(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=b"a"), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0, payload=b"b"), offset=1),
        # Same sequence, DIFFERENT payload -- conflicting redelivery, run-A only.
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=b"DIFFERENT"), offset=2),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1, payload=b"b"), offset=3),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=4, idle_timeout_seconds=0.1))

    assert "run-A" in r.failed_runs
    assert "SequenceIntegrityError" in r.failed_runs["run-A"]
    assert "run-B" not in r.failed_runs
    assert r.active_run_states()["run-B"].message_count == 2


# ---------------------------------------------------------------------
# One run's failure must not corrupt another run's writer
# ---------------------------------------------------------------------


def test_one_run_failure_does_not_corrupt_another_runs_writer_or_disk_state(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-good", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=0), offset=1, partition=0),
        _consumed(_envelope(robot_run_id="run-good", sequence_number=1), offset=2),
        # run-bad's next message arrives on a DIFFERENT partition --
        # PartitionInvariantError, run-bad only.
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=1), offset=0, partition=1),
        _consumed(_envelope(robot_run_id="run-good", sequence_number=2), offset=3),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=5, idle_timeout_seconds=0.1)
        return await r.finalize_run("run-good")

    result = asyncio.run(scenario())

    assert result.message_count == 3
    assert "PartitionInvariantError" in r.failed_runs["run-bad"]
    # run-bad's partial state was discarded, never finalized.
    assert not partial_bag_path(tmp_path, "run-bad").exists()
    assert not final_bag_path(tmp_path, "run-bad").exists()
    # run-good's own finalized file is completely intact.
    messages = _read_mcap_messages(result.path)
    assert len(messages) == 3


def test_late_message_for_an_already_failed_run_is_dropped_not_reopened(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=0), offset=0),
        # Gap -- run-bad fails and is abandoned.
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=5), offset=1),
        # A later, well-formed message for the SAME robot_run_id must
        # not silently reopen it with fresh state.
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=0), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=3, idle_timeout_seconds=0.1))

    assert "run-bad" not in r.active_run_states()
    assert "run-bad" in r.failed_runs


def test_poison_undecodable_record_is_recorded_not_fatal_and_does_not_stop_routing(
    tmp_path, monkeypatch
) -> None:
    """A record that fails to decode at all has no reliable
    robot_run_id -- it cannot be isolated per-run the way a
    SequenceIntegrityError/PartitionInvariantError can, so it must not
    take down ingestion for every OTHER currently-active run either."""
    queue = [
        _consumed(_envelope(robot_run_id="run-good", sequence_number=0), offset=0),
        EnvelopeDecodeError("missing required header(s): ...", topic="t", partition=0, offset=1),
        _consumed(_envelope(robot_run_id="run-good", sequence_number=1), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        consumed = await r.run_for(max_messages=3, idle_timeout_seconds=0.1)
        assert consumed == 3
        return await r.finalize_run("run-good")

    result = asyncio.run(scenario())

    assert result.message_count == 2
    assert len(r.stats.poison_messages) == 1
    assert r.stats.poison_messages[0] == {
        "topic": "t",
        "partition": 0,
        "offset": 1,
        "error": "missing required header(s): ... [t:0@1]",
    }
    assert r.failed_runs == {}  # a poison record belongs to no run


# ---------------------------------------------------------------------
# Resource bound
# ---------------------------------------------------------------------


def test_max_active_runs_is_enforced_and_fails_loudly(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-C", sequence_number=0), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, max_active_runs=2)

    async def scenario():
        await r.run_once()
        await r.run_once()
        await r.run_once()  # run-C: over the limit

    with pytest.raises(MaxActiveRunsExceededError):
        asyncio.run(scenario())

    assert r.active_run_count == 2
    assert "run-A" in r.active_run_states()
    assert "run-B" in r.active_run_states()


def test_max_active_runs_does_not_block_a_third_run_once_one_finalizes(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-C", sequence_number=0), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, max_active_runs=2)

    async def scenario():
        await r.run_once()  # run-A
        await r.run_once()  # run-B
        await r.finalize_run("run-A")  # frees a slot
        await r.run_once()  # run-C now fits

    asyncio.run(scenario())

    assert r.active_run_count == 2
    assert "run-B" in r.active_run_states()
    assert "run-C" in r.active_run_states()
    assert "run-A" in r.finalized_runs


# ---------------------------------------------------------------------
# Finalize one run while others remain active / finalize all
# ---------------------------------------------------------------------


def test_finalize_run_leaves_other_active_runs_untouched(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=3, idle_timeout_seconds=0.1)
        return await r.finalize_run("run-A")

    result = asyncio.run(scenario())

    assert result.robot_run_id == "run-A"
    assert "run-A" not in r.active_run_states()
    assert "run-B" in r.active_run_states()
    assert r.active_run_states()["run-B"].message_count == 2


def test_finalize_run_on_unknown_run_id_raises(tmp_path, monkeypatch) -> None:
    _install_fake_consumer(monkeypatch, [])
    r = _make_router(tmp_path)

    async def scenario():
        await r.finalize_run("never-existed")

    with pytest.raises(UnknownRunError):
        asyncio.run(scenario())


def test_finalize_all_finalizes_every_active_run(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-C", sequence_number=0), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=3, idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    assert set(results) == {"run-A", "run-B", "run-C"}
    assert r.active_run_count == 0
    assert set(r.finalized_runs) == {"run-A", "run-B", "run-C"}


# ---------------------------------------------------------------------
# Kafka offset-commit safety policy
# ---------------------------------------------------------------------


def test_commit_safe_never_advances_past_an_active_runs_first_offset(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=1), offset=2),
    ]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=3, idle_timeout_seconds=0.1)
        # Finalizing run-A must NOT let the commit boundary pass
        # run-B's first (still-unfinalized) offset (1).
        await r.finalize_run("run-A")

    asyncio.run(scenario())

    fake_consumer = created[0]
    assert fake_consumer.commit_offsets_calls[-1] == {0: 1}


def test_commit_safe_advances_past_everything_once_all_runs_finalized(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
    ]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=2, idle_timeout_seconds=0.1)
        await r.finalize_all()

    asyncio.run(scenario())

    fake_consumer = created[0]
    # Both runs finalized -- safe to advance past the last consumed
    # offset (1) entirely: next-to-read = 2.
    assert fake_consumer.commit_offsets_calls[-1] == {0: 2}


def test_commit_safe_is_monotonic_non_decreasing_across_successive_finalizes(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-C", sequence_number=0), offset=2),
    ]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=3, idle_timeout_seconds=0.1)
        await r.finalize_run("run-B")  # A(0), C(2) still active -> safe = min(0,2) = 0
        await r.finalize_run("run-A")  # only C(2) active -> safe = 2
        await r.finalize_run("run-C")  # none active -> safe = last(2)+1 = 3

    asyncio.run(scenario())

    fake_consumer = created[0]
    seen = [call[0] for call in fake_consumer.commit_offsets_calls]
    assert seen == [0, 2, 3]


# ---------------------------------------------------------------------
# close() does not silently finalize
# ---------------------------------------------------------------------


def test_close_does_not_finalize_active_runs(tmp_path, monkeypatch) -> None:
    queue = [_consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0)]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=1, idle_timeout_seconds=0.1)
        await r.close()

    asyncio.run(scenario())

    assert created[0].closed is True
    assert "run-A" in r.active_run_states()  # never finalized
    assert partial_bag_path(tmp_path, "run-A").exists()
    assert not final_bag_path(tmp_path, "run-A").exists()
