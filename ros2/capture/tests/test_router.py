"""Unit tests for router.py's ContinuousCaptureRouter: interleaved
multi-RobotRun routing, explicit CaptureSession lifecycle (RUN_START/
RUN_END control events, idle-timeout fallback), per-session sequence/
partition isolation, per-session failure isolation, resource bounds,
finalization, and the Kafka offset-commit-safety policy -- using a
fake in-process Kafka consumer (no real broker) but the REAL
McapCaptureWriter/finalize/validate path, matching
test_capture_consumer.py's own convention.

Runs only inside the ros2 container (needs the ROS 2 interface definitions + mcap).
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
from sceneops_streaming.errors import EnvelopeDecodeError  # noqa: E402

import router as router_module  # noqa: E402
from finalize import final_bag_path, partial_bag_path  # noqa: E402
from router import (  # noqa: E402
    ROUTER_CONSUMER_GROUP_ID,
    ContinuousCaptureRouter,
    FinalizationReason,
    MaxActiveRunsExceededError,
    RunIdentityConflictError,
    SessionState,
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


def _run_start(robot_run_id: str, *, robot_id: str = "robot-1", partition: int = 0, offset: int = 0):
    return _consumed(
        build_control_envelope(
            event_type=RunEventType.RUN_START, robot_id=robot_id, robot_run_id=robot_run_id
        ),
        partition=partition,
        offset=offset,
    )


def _run_end(robot_run_id: str, *, robot_id: str = "robot-1", partition: int = 0, offset: int = 0):
    return _consumed(
        build_control_envelope(
            event_type=RunEventType.RUN_END, robot_id=robot_id, robot_run_id=robot_run_id
        ),
        partition=partition,
        offset=offset,
    )


def _read_mcap_messages(path):
    out = []
    with open(path, "rb") as f:
        reader = make_reader(f)
        for _schema, channel, message in reader.iter_messages():
            out.append((channel.topic, message.log_time, message.publish_time, message.data))
    return out


class _FakeClock:
    """Deterministic, manually-advanced clock -- avoids sleep-heavy
    idle-timeout tests entirely."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


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


def _make_router(
    tmp_path, *, max_active_runs: int = 64, session_idle_timeout_seconds=None, clock=None
) -> ContinuousCaptureRouter:
    return ContinuousCaptureRouter(
        settings=object(),
        output_root=tmp_path,
        max_active_runs=max_active_runs,
        session_idle_timeout_seconds=session_idle_timeout_seconds,
        clock=clock or (lambda: 0.0),
    )


# ---------------------------------------------------------------------
# Explicit start -> telemetry -> end
# ---------------------------------------------------------------------


def test_explicit_start_telemetry_end_lifecycle(tmp_path, monkeypatch) -> None:
    queue = [
        _run_start("run-1"),
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _consumed(_envelope(robot_run_id="run-1", sequence_number=1)),
        _run_end("run-1"),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=4, loop_idle_timeout_seconds=0.1))

    states = r.session_states()
    assert states["run-1"].state == SessionState.FINALIZED
    assert states["run-1"].finalization_reason == FinalizationReason.EXPLICIT_RUN_END
    assert states["run-1"].message_count == 2
    result = r.finalized_runs["run-1"]
    assert result.message_count == 2
    messages = _read_mcap_messages(result.path)
    assert len(messages) == 2


def test_run_start_alone_leaves_session_in_discovered_state(tmp_path, monkeypatch) -> None:
    queue = [_run_start("run-1")]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=1, loop_idle_timeout_seconds=0.1))

    assert r.session_states()["run-1"].state == SessionState.DISCOVERED


def test_run_end_immediately_after_run_start_finalizes_empty_session(tmp_path, monkeypatch) -> None:
    queue = [_run_start("run-1"), _run_end("run-1")]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=2, loop_idle_timeout_seconds=0.1))

    state = r.session_states()["run-1"]
    assert state.state == SessionState.FINALIZED
    assert state.message_count == 0
    assert "run-1" not in r.finalized_runs  # no CaptureResult -- nothing was written
    assert not partial_bag_path(tmp_path, "run-1").exists()
    assert not final_bag_path(tmp_path, "run-1").exists()


# ---------------------------------------------------------------------
# Telemetry before explicit start (implicit discovery) -- supported
# ---------------------------------------------------------------------


def test_telemetry_before_explicit_start_implicitly_discovers_session(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _consumed(_envelope(robot_run_id="run-1", sequence_number=1)),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=2, loop_idle_timeout_seconds=0.1))

    assert r.session_states()["run-1"].state == SessionState.RECORDING
    assert r.session_states()["run-1"].message_count == 2


# ---------------------------------------------------------------------
# Duplicate start / end
# ---------------------------------------------------------------------


def test_duplicate_run_start_is_idempotent_no_op(tmp_path, monkeypatch) -> None:
    queue = [
        _run_start("run-1"),
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _run_start("run-1"),  # duplicate -- must not reset state/tracker
        _consumed(_envelope(robot_run_id="run-1", sequence_number=1)),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=4, loop_idle_timeout_seconds=0.1))

    state = r.session_states()["run-1"]
    assert state.state == SessionState.RECORDING
    assert state.message_count == 2
    assert any(
        e["event"] == "RUN_START" and e["reason"] == "duplicate"
        for e in r.stats.control_events_ignored
    )


def test_duplicate_run_end_after_finalization_is_idempotent_no_op(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _run_end("run-1"),
        _run_end("run-1"),  # duplicate -- must not raise or re-finalize
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1))

    assert r.session_states()["run-1"].state == SessionState.FINALIZED
    assert any(
        e["event"] == "RUN_END" and e["reason"] == "already_finalized"
        for e in r.stats.control_events_ignored
    )


def test_run_end_for_unknown_run_is_recorded_not_an_error(tmp_path, monkeypatch) -> None:
    queue = [_run_end("never-seen")]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=1, loop_idle_timeout_seconds=0.1))

    assert "never-seen" not in r.session_states()
    assert any(
        e["robot_run_id"] == "never-seen" and e["reason"] == "unknown_run"
        for e in r.stats.control_events_ignored
    )


# ---------------------------------------------------------------------
# Telemetry after finalization
# ---------------------------------------------------------------------


def test_telemetry_after_finalization_is_dropped_and_recorded(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _run_end("run-1"),
        # Late telemetry after finalization -- must not reopen or crash.
        _consumed(_envelope(robot_run_id="run-1", sequence_number=1)),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1))

    state = r.session_states()["run-1"]
    assert state.state == SessionState.FINALIZED
    assert state.message_count == 1  # late message never written
    assert r.stats.messages_after_finalization == 1


# ---------------------------------------------------------------------
# Interleaved sessions with control events
# ---------------------------------------------------------------------


def test_interleaved_sessions_with_independent_control_events(tmp_path, monkeypatch) -> None:
    queue = [
        _run_start("run-A"),
        _run_start("run-B"),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0)),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0)),
        _run_end("run-A"),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1)),
        _run_end("run-B"),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=7, loop_idle_timeout_seconds=0.1))

    a = r.session_states()["run-A"]
    b = r.session_states()["run-B"]
    assert a.state == SessionState.FINALIZED and a.message_count == 1
    assert b.state == SessionState.FINALIZED and b.message_count == 2
    assert r.finalized_runs["run-A"].message_count == 1
    assert r.finalized_runs["run-B"].message_count == 2


# ---------------------------------------------------------------------
# Idle-timeout finalization (fake clock, no sleeping)
# ---------------------------------------------------------------------


def test_idle_timeout_finalizes_a_quiet_session_through_the_durable_path(
    tmp_path, monkeypatch
) -> None:
    clock = _FakeClock(start=0.0)
    queue = [_consumed(_envelope(robot_run_id="run-1", sequence_number=0))]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, session_idle_timeout_seconds=10.0, clock=clock)

    async def scenario():
        await r.run_once()  # consumes the one telemetry message, session RECORDING
        clock.advance(11.0)  # past the 10s idle threshold
        await r.run_once()  # empty poll -- but still checks idle sessions

    asyncio.run(scenario())

    state = r.session_states()["run-1"]
    assert state.state == SessionState.FINALIZED
    assert state.finalization_reason == FinalizationReason.IDLE_TIMEOUT
    result = r.finalized_runs["run-1"]
    assert result.message_count == 1


def test_idle_timeout_disabled_by_default_leaves_session_open_indefinitely(
    tmp_path, monkeypatch
) -> None:
    clock = _FakeClock(start=0.0)
    queue = [_consumed(_envelope(robot_run_id="run-1", sequence_number=0))]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, clock=clock)  # session_idle_timeout_seconds=None (default)

    async def scenario():
        await r.run_once()
        clock.advance(10_000.0)
        await r.run_once()

    asyncio.run(scenario())

    assert r.session_states()["run-1"].state == SessionState.RECORDING


def test_idle_timeout_does_not_affect_a_session_still_receiving_activity(
    tmp_path, monkeypatch
) -> None:
    clock = _FakeClock(start=0.0)
    queue = [
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _consumed(_envelope(robot_run_id="run-1", sequence_number=1)),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, session_idle_timeout_seconds=10.0, clock=clock)

    async def scenario():
        await r.run_once()  # seq 0 -- last_activity_at = 0
        clock.advance(9.0)  # just under threshold
        await r.run_once()  # seq 1 -- last_activity_at = 9, touch() refreshes it
        clock.advance(9.0)  # 18 total, but only 9 since the LAST activity

    asyncio.run(scenario())

    assert r.session_states()["run-1"].state == SessionState.RECORDING


# ---------------------------------------------------------------------
# Explicit-end vs timeout race
# ---------------------------------------------------------------------


def test_explicit_end_racing_idle_timeout_resolves_deterministically(
    tmp_path, monkeypatch
) -> None:
    """RUN_END is processed (and finalizes the session) BEFORE the
    idle-timeout check in the same run_once() call -- so an explicit
    end that arrives exactly when a session would also be timing out
    always wins, and the timeout check that follows becomes a no-op
    (the session is already terminal)."""
    clock = _FakeClock(start=0.0)
    queue = [
        _consumed(_envelope(robot_run_id="run-1", sequence_number=0)),
        _run_end("run-1"),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, session_idle_timeout_seconds=10.0, clock=clock)

    async def scenario():
        await r.run_once()  # seq 0
        clock.advance(11.0)  # already past idle threshold
        await r.run_once()  # RUN_END processed THEN idle-check runs -- no double-finalize

    asyncio.run(scenario())

    state = r.session_states()["run-1"]
    assert state.state == SessionState.FINALIZED
    assert state.finalization_reason == FinalizationReason.EXPLICIT_RUN_END
    assert len(r.finalized_runs) == 1  # finalized exactly once, not twice


def test_idle_timeout_winning_the_race_makes_a_later_run_end_a_no_op(
    tmp_path, monkeypatch
) -> None:
    """The opposite ordering: idle-timeout fires first (no RUN_END
    buffered yet), and a RUN_END that arrives afterward for an
    already-FINALIZED session is a harmless duplicate."""
    clock = _FakeClock(start=0.0)
    queue = [_consumed(_envelope(robot_run_id="run-1", sequence_number=0))]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, session_idle_timeout_seconds=10.0, clock=clock)

    async def scenario():
        await r.run_once()  # seq 0
        clock.advance(11.0)
        await r.run_once()  # nothing buffered -- idle-timeout fires here
        assert r.session_states()["run-1"].finalization_reason == FinalizationReason.IDLE_TIMEOUT
        # A RUN_END shows up late (redelivery, or a slow producer) --
        # must be a harmless no-op against the already-finalized session.
        r._consumer._queue.append(_run_end("run-1"))
        await r.run_once()

    asyncio.run(scenario())

    state = r.session_states()["run-1"]
    assert state.finalization_reason == FinalizationReason.IDLE_TIMEOUT  # unchanged
    assert len(r.finalized_runs) == 1
    assert any(
        e["event"] == "RUN_END" and e["reason"] == "already_finalized"
        for e in r.stats.control_events_ignored
    )


# ---------------------------------------------------------------------
# Offset frontier release (§5's own A/B/C scenario)
# ---------------------------------------------------------------------


def test_offset_frontier_release_with_mixed_termination_paths(tmp_path, monkeypatch) -> None:
    """A -> finalized (explicit), C -> finalized (explicit), B -> idle
    timeout -- the commit frontier must eventually reach the latest
    safe offset once every session has reached a terminal state,
    regardless of which lifecycle path got each of them there."""
    clock = _FakeClock(start=0.0)
    queue = [
        _consumed(_envelope(robot_run_id="A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="C", sequence_number=0), offset=2),
    ]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, session_idle_timeout_seconds=10.0, clock=clock)

    async def scenario():
        await r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1)
        # A and C active (first_offset 0 and 2), B active (first_offset 1)
        # -- safe boundary bounded by min(0, 1, 2) = 0 right now.
        await r.finalize_run("A")
        # A gone -- safe boundary now min(1, 2) = 1.
        await r.finalize_run("C")
        # C gone -- only B left -- safe boundary still bounded by B's
        # first_offset = 1 (B not yet terminal).
        assert created[0].commit_offsets_calls[-1] == {0: 1}
        clock.advance(11.0)
        await r.check_idle_sessions()  # B times out -- last session gone

    asyncio.run(scenario())

    fake_consumer = created[0]
    assert r.session_states()["B"].finalization_reason == FinalizationReason.IDLE_TIMEOUT
    # Every session terminal now -- safe boundary reaches last_consumed+1 = 3.
    assert fake_consumer.commit_offsets_calls[-1] == {0: 3}


def test_permanently_failed_session_no_longer_blocks_frontier(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-good", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=0), offset=1),
        # run-bad gets a gap -- SequenceIntegrityError, FAILED (terminal).
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=5), offset=2),
        _consumed(_envelope(robot_run_id="run-good", sequence_number=1), offset=3),
    ]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=4, loop_idle_timeout_seconds=0.1)
        await r.finalize_run("run-good")

    asyncio.run(scenario())

    assert r.session_states()["run-bad"].state == SessionState.FAILED
    fake_consumer = created[0]
    # run-bad is terminal (FAILED) and run-good just finalized -- the
    # safe boundary reaches all the way to last_consumed(3)+1 = 4, never
    # permanently stuck at run-bad's own first_offset (1).
    assert fake_consumer.commit_offsets_calls[-1] == {0: 4}


# ---------------------------------------------------------------------
# One failed session not blocking unrelated sessions forever
# ---------------------------------------------------------------------


def test_one_failed_session_does_not_block_other_sessions_forever(tmp_path, monkeypatch) -> None:
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
        await r.run_for(max_messages=5, loop_idle_timeout_seconds=0.1)
        return await r.finalize_run("run-good")

    result = asyncio.run(scenario())

    assert result.message_count == 3
    assert r.session_states()["run-bad"].state == SessionState.FAILED
    assert not partial_bag_path(tmp_path, "run-bad").exists()
    assert not final_bag_path(tmp_path, "run-bad").exists()
    messages = _read_mcap_messages(result.path)
    assert len(messages) == 3


def test_late_message_for_an_already_failed_session_is_dropped_not_reopened(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=5), offset=1),  # gap -> FAILED
        _consumed(_envelope(robot_run_id="run-bad", sequence_number=0), offset=2),  # late, well-formed
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1))

    assert r.session_states()["run-bad"].state == SessionState.FAILED
    assert r.stats.messages_after_finalization == 1


def test_poison_undecodable_record_is_recorded_not_fatal_and_does_not_stop_routing(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-good", sequence_number=0), offset=0),
        EnvelopeDecodeError("missing required header(s): ...", topic="t", partition=0, offset=1),
        _consumed(_envelope(robot_run_id="run-good", sequence_number=1), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        consumed = await r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1)
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


# ---------------------------------------------------------------------
# Sequence/isolation, byte-exactness, timestamp mapping, no cross-run
# ---------------------------------------------------------------------


def test_independent_gap_detection_only_affects_the_gapped_session(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=5), offset=2),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1), offset=3),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=2), offset=4),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=5, loop_idle_timeout_seconds=0.1))

    assert r.session_states()["run-A"].state == SessionState.FAILED
    assert r.session_states()["run-B"].state == SessionState.RECORDING
    assert r.session_states()["run-B"].message_count == 3


def test_independent_duplicate_handling_per_session(tmp_path, monkeypatch) -> None:
    payload_a = bytes([0xAA])
    payload_b = bytes([0xBB])
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=payload_a), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0, payload=payload_b), offset=1),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=payload_a), offset=2),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0, payload=payload_b), offset=3),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=1, payload=payload_a), offset=4),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=5, loop_idle_timeout_seconds=0.1))

    assert r.stats.messages_skipped_duplicate == 2
    assert r.session_states()["run-A"].message_count == 2
    assert r.session_states()["run-B"].message_count == 1


def test_conflicting_duplicate_rejected_and_isolated_to_its_own_session(
    tmp_path, monkeypatch
) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=b"a"), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0, payload=b"b"), offset=1),
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0, payload=b"DIFFERENT"), offset=2),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1, payload=b"b"), offset=3),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    asyncio.run(r.run_for(max_messages=4, loop_idle_timeout_seconds=0.1))

    assert r.session_states()["run-A"].state == SessionState.FAILED
    assert r.session_states()["run-B"].message_count == 2


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
        await r.run_for(max_messages=16, loop_idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    a_messages = _read_mcap_messages(results["run-A"].path)
    b_messages = _read_mcap_messages(results["run-B"].path)
    assert len(a_messages) == 8 and len(b_messages) == 8
    a_payloads = {data for _, _, _, data in a_messages}
    b_payloads = {data for _, _, _, data in b_messages}
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
    import time as _time

    before_ns = _time.time_ns()

    async def scenario():
        await r.run_for(max_messages=1, loop_idle_timeout_seconds=0.1)
        return await r.finalize_run("run-A")

    result = asyncio.run(scenario())
    [(channel, log_time, publish_time, payload)] = _read_mcap_messages(result.path)

    assert channel == "/vehicle/odom"
    assert payload == bytes(range(256))
    # log_time is the router's receive time, never the source timestamp;
    # publish_time is the envelope's ingest time.
    assert log_time != 1_700_000_000_123_000_000
    assert log_time >= before_ns
    assert publish_time == 1_700_000_000_456_000_000


def test_many_interleaved_sessions_all_independently_correct(tmp_path, monkeypatch) -> None:
    num_runs = 12
    per_run = 6
    queue = []
    offset = 0
    for seq in range(per_run):
        for run_idx in range(num_runs):
            queue.append(
                _consumed(
                    _envelope(robot_run_id=f"run-{run_idx}", sequence_number=seq), offset=offset
                )
            )
            offset += 1
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path, max_active_runs=num_runs)

    async def scenario():
        await r.run_for(max_messages=num_runs * per_run, loop_idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    assert len(results) == num_runs
    for run_idx in range(num_runs):
        assert results[f"run-{run_idx}"].message_count == per_run


# ---------------------------------------------------------------------
# Active-run capacity reuse / resource bound
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


def test_active_run_capacity_reuse_after_finalization(tmp_path, monkeypatch) -> None:
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
    assert r.session_states()["run-A"].state == SessionState.FINALIZED


# ---------------------------------------------------------------------
# Finalize one run while others remain active / finalize all / unknown
# ---------------------------------------------------------------------


def test_finalize_run_leaves_other_sessions_untouched(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=1), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1)
        return await r.finalize_run("run-A")

    result = asyncio.run(scenario())

    assert result.robot_run_id == "run-A"
    assert r.session_states()["run-A"].state == SessionState.FINALIZED
    assert r.session_states()["run-B"].state == SessionState.RECORDING
    assert r.session_states()["run-B"].message_count == 2


def test_finalize_run_on_unknown_run_id_raises(tmp_path, monkeypatch) -> None:
    _install_fake_consumer(monkeypatch, [])
    r = _make_router(tmp_path)

    async def scenario():
        await r.finalize_run("never-existed")

    with pytest.raises(UnknownRunError):
        asyncio.run(scenario())


def test_finalize_run_on_already_finalized_run_id_raises(tmp_path, monkeypatch) -> None:
    queue = [_consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0)]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_once()
        await r.finalize_run("run-A")
        await r.finalize_run("run-A")  # already terminal -- must not silently reopen

    with pytest.raises(UnknownRunError):
        asyncio.run(scenario())


def test_finalize_all_finalizes_every_non_terminal_session(tmp_path, monkeypatch) -> None:
    queue = [
        _consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0),
        _consumed(_envelope(robot_run_id="run-B", sequence_number=0), offset=1),
        _consumed(_envelope(robot_run_id="run-C", sequence_number=0), offset=2),
    ]
    _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=3, loop_idle_timeout_seconds=0.1)
        return await r.finalize_all()

    results = asyncio.run(scenario())

    assert set(results) == {"run-A", "run-B", "run-C"}
    assert r.active_run_count == 0


# ---------------------------------------------------------------------
# close() does not silently finalize
# ---------------------------------------------------------------------


def test_close_does_not_finalize_active_sessions(tmp_path, monkeypatch) -> None:
    queue = [_consumed(_envelope(robot_run_id="run-A", sequence_number=0), offset=0)]
    created = _install_fake_consumer(monkeypatch, queue)
    r = _make_router(tmp_path)

    async def scenario():
        await r.run_for(max_messages=1, loop_idle_timeout_seconds=0.1)
        await r.close()

    asyncio.run(scenario())

    assert created[0].closed is True
    assert r.session_states()["run-A"].state == SessionState.RECORDING
    assert partial_bag_path(tmp_path, "run-A").exists()
    assert not final_bag_path(tmp_path, "run-A").exists()
