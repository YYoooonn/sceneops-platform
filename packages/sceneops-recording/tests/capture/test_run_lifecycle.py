"""The RUN_START -> telemetry -> RUN_END lifecycle contract of a lifecycle-governed
capture (``run_capture(stop_on_run_end=True)``), docs/architecture/streaming-transport.md
§26. A fake in-process consumer feeds the real MCAP writer / finalize / receipt path;
the idle tests drive the guard's clock explicitly. The Kafka behavior the contract
depends on (Capture records no progress in Kafka and reads from the position it is given)
is proven against a real broker in test_lifecycle_integration.py.

Runs only inside the capture image (needs the ROS 2 interface definitions + mcap).
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from sceneops_streaming import (
    ConsumedTelemetryEnvelope,
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    build_control_envelope,
)

import sceneops_recording.capture.consumer as capture_consumer
from sceneops_recording.capture.consumer import (
    RunEndNotObservedError,
    RunStartNotObservedError,
    SequenceIntegrityError,
    run_capture,
)
from sceneops_recording.capture.finalize import (
    FinalBagExistsError,
    final_bag_path,
    partial_bag_path,
)
from sceneops_recording.capture.validation import McapValidationError
from sceneops_recording.capture.lifecycle import (
    RunIncompleteError,
    RunLifecycle,
    RunLifecycleError,
    RunPhase,
)
from sceneops_recording.capture_receipt import FinalizationReason
from sceneops_recording.capture.receipt_io import read_capture_receipt

START = RunEventType.RUN_START
END = RunEventType.RUN_END


# ---------------------------------------------------------------------
# RunLifecycle state machine
# ---------------------------------------------------------------------


def test_lifecycle_normal_path() -> None:
    lifecycle = RunLifecycle()
    assert lifecycle.phase is RunPhase.ARMED
    lifecycle.on_control(START, redelivery=False)
    assert lifecycle.phase is RunPhase.CAPTURING
    lifecycle.on_telemetry()
    lifecycle.on_control(END, redelivery=False)
    assert lifecycle.phase is RunPhase.ENDED


def test_lifecycle_rejects_telemetry_before_run_start() -> None:
    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        RunLifecycle().on_telemetry()


def test_lifecycle_rejects_run_end_before_run_start() -> None:
    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        RunLifecycle().on_control(END, redelivery=False)


def test_lifecycle_rejects_a_second_run_start() -> None:
    lifecycle = RunLifecycle()
    lifecycle.on_control(START, redelivery=False)
    lifecycle.on_telemetry()
    with pytest.raises(RunLifecycleError, match="exactly one streaming session"):
        lifecycle.on_control(START, redelivery=False)


def test_lifecycle_accepts_only_an_immediate_run_start_redelivery() -> None:
    lifecycle = RunLifecycle()
    lifecycle.on_control(START, redelivery=False)
    lifecycle.on_control(START, redelivery=True)
    lifecycle.on_control(START, redelivery=True)
    assert lifecycle.phase is RunPhase.CAPTURING

    lifecycle.on_telemetry()
    with pytest.raises(RunLifecycleError, match="immediate redelivery"):
        lifecycle.on_control(START, redelivery=True)


def test_lifecycle_rejects_a_run_end_that_repeats_run_start_exactly() -> None:
    lifecycle = RunLifecycle()
    lifecycle.on_control(START, redelivery=False)
    with pytest.raises(RunLifecycleError, match="immediate redelivery"):
        lifecycle.on_control(END, redelivery=True)


def test_lifecycle_rejects_records_after_run_end() -> None:
    lifecycle = RunLifecycle()
    lifecycle.on_control(START, redelivery=False)
    lifecycle.on_control(END, redelivery=False)
    with pytest.raises(RunLifecycleError, match="follows RUN_END"):
        lifecycle.on_telemetry()
    with pytest.raises(RunLifecycleError):
        lifecycle.on_control(START, redelivery=False)


def test_lifecycle_ignores_unrecognized_control_events() -> None:
    lifecycle = RunLifecycle()
    lifecycle.on_control(None, redelivery=False)
    assert lifecycle.phase is RunPhase.ARMED


# ---------------------------------------------------------------------
# run_capture, lifecycle-governed
# ---------------------------------------------------------------------


def _telemetry(run_id: str = "run-1", **overrides) -> TelemetryEnvelope:
    fields = dict(
        robot_id="robot-1",
        robot_run_id=run_id,
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


def _record(envelope: TelemetryEnvelope, offset: int) -> ConsumedTelemetryEnvelope:
    return ConsumedTelemetryEnvelope(
        envelope=envelope,
        key=envelope.robot_run_id.encode("utf-8"),
        topic="sceneops.robot.telemetry.v1",
        partition=0,
        offset=offset,
    )


def _t(seq: int, offset: int, run_id: str = "run-1", **overrides):
    return _record(_telemetry(run_id, sequence_number=seq, **overrides), offset)


def _c(event: RunEventType, seq: int, offset: int, run_id: str = "run-1", **kw):
    return _record(
        build_control_envelope(
            event_type=event,
            robot_id="robot-1",
            robot_run_id=run_id,
            sequence_number=seq,
            **kw,
        ),
        offset,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0


class _ProcessDied(Exception):
    pass


class _FakeConsumer:
    """Serves the queue; each poll advances the clock by ``tick`` seconds, so a
    run of polls is a span of wall time for the idle guard."""

    def __init__(self, queue, clock: _Clock, tick: float):
        self._queue = list(queue)
        self._clock = clock
        self._tick = tick
        self.closed = False

    @property
    def remaining(self) -> int:
        return len(self._queue)

    async def poll(self, timeout_seconds: float):
        self._clock.now += self._tick
        return self._queue.pop(0) if self._queue else None

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(capture_consumer, "_monotonic", lambda: fake.now)
    return fake


@pytest.fixture
def install(monkeypatch, clock):
    created: list[_FakeConsumer] = []

    def _install(queue, *, tick: float = 0.0):
        def factory(*, start_from_beginning, **_kwargs):
            assert start_from_beginning is True
            consumer = _FakeConsumer(queue, clock, tick)
            created.append(consumer)
            return consumer

        monkeypatch.setattr(capture_consumer, "KafkaTelemetryConsumer", factory)
        return created

    return _install


def _capture(tmp_path, **overrides):
    kwargs = dict(
        settings=object(),
        robot_id="robot-1",
        robot_run_id="run-1",
        output_root=tmp_path,
        stop_on_run_end=True,
    )
    kwargs.update(overrides)
    return asyncio.run(run_capture(**kwargs))


def _assert_nothing_finalized(tmp_path, consumer) -> None:
    assert consumer.closed is True
    assert not final_bag_path(tmp_path, "run-1").exists()


def _normal_run(first_offset: int = 0):
    return [
        _c(START, 0, first_offset),
        _t(0, first_offset + 1),
        _t(1, first_offset + 2),
        _c(END, 1, first_offset + 3, telemetry_count=2),
    ]


def test_normal_run_finalizes_on_run_end(tmp_path, install) -> None:
    install(_normal_run())

    result = _capture(tmp_path)

    assert result.message_count == 2
    assert (result.first_offset, result.last_offset) == (0, 3)
    receipt = read_capture_receipt(final_bag_path(tmp_path, "run-1"))
    assert receipt.finalization.reason is FinalizationReason.EXPLICIT_RUN_END


def test_telemetry_before_run_start_fails_at_the_first_record(
    tmp_path, install
) -> None:
    queue = [
        _t(0, 0),
        _t(1, 1),
        _c(START, 0, 2),
        _t(2, 3),
        _c(END, 1, 4, telemetry_count=3),
    ]
    created = install(queue)

    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])
    assert created[0].remaining == len(queue) - 1  # failed on record 0, read no more
    assert partial_bag_path(tmp_path, "run-1").exists()


def test_missing_run_start_with_run_end_present_fails_on_the_first_record(
    tmp_path, install
) -> None:
    """The bridge's RUN_START never reached the topic: telemetry starts at
    sequence 0 and RUN_END is control sequence 1. The sequence trackers alone
    only notice this at RUN_END ("first control sequence was 1")."""
    created = install([_t(0, 0), _t(1, 1), _c(END, 1, 2, telemetry_count=2)])

    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_run_end_without_run_start_is_rejected(tmp_path, install) -> None:
    created = install([_c(END, 0, 0, telemetry_count=0)])

    with pytest.raises(RunLifecycleError, match="RUN_END in phase armed"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_start_after_run_start_is_a_lifecycle_failure_not_a_sequence_error(
    tmp_path, install
) -> None:
    """A start position past RUN_START: the first record of the run it reads
    is mid-run telemetry."""
    created = install([_t(3, 5), _t(4, 6), _c(END, 1, 7, telemetry_count=5)])

    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_start_before_run_start_skips_other_runs_and_captures_the_run(
    tmp_path, install
) -> None:
    """A start position that is only a lower bound: history of other runs
    (their control events and telemetry included) precedes RUN_START."""
    queue = [
        _c(START, 0, 0, run_id="other"),
        _t(0, 1, run_id="other"),
        _c(END, 1, 2, run_id="other", telemetry_count=0),
        *_normal_run(first_offset=3),
    ]
    install(queue)

    result = _capture(tmp_path)

    assert (result.first_offset, result.last_offset) == (3, 6)


def test_missing_run_end_is_not_finalized(tmp_path, install) -> None:
    created = install(_normal_run()[:-1], tick=1.0)

    with pytest.raises(RunEndNotObservedError):
        _capture(tmp_path, idle_timeout_seconds=10)

    _assert_nothing_finalized(tmp_path, created[0])
    assert partial_bag_path(tmp_path, "run-1").exists()


def test_immediate_duplicate_run_start_is_skipped(tmp_path, install) -> None:
    queue = [
        _c(START, 0, 0),
        _c(START, 0, 1),
        _t(0, 2),
        _c(END, 1, 3, telemetry_count=1),
    ]
    install(queue)

    result = _capture(tmp_path)

    assert result.message_count == 1


def test_run_start_repeated_after_telemetry_is_a_second_session(
    tmp_path, install
) -> None:
    queue = [
        _c(START, 0, 0),
        _t(0, 1),
        _c(START, 0, 2),
        _t(1, 3),
        _c(END, 1, 4, telemetry_count=2),
    ]
    created = install(queue)

    with pytest.raises(RunLifecycleError, match="immediate redelivery"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_second_run_start_with_the_next_control_sequence_is_rejected(
    tmp_path, install
) -> None:
    queue = [
        _c(START, 0, 0),
        _t(0, 1),
        _c(START, 1, 2),
        _t(1, 3),
        _c(END, 2, 4, telemetry_count=2),
    ]
    created = install(queue)

    with pytest.raises(RunLifecycleError, match="exactly one streaming session"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_run_end_at_run_starts_control_sequence_is_a_conflict(
    tmp_path, install
) -> None:
    """RUN_END declares a count, so its payload can never equal RUN_START's: at
    the same control sequence the tracker rejects it as a conflicting
    redelivery instead of skipping it as a duplicate."""
    created = install([_c(START, 0, 0), _t(0, 1), _c(END, 0, 2, telemetry_count=1)])

    with pytest.raises(SequenceIntegrityError, match="conflicting redelivery"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_telemetry_gap_inside_a_started_run_fails(tmp_path, install) -> None:
    created = install(
        [_c(START, 0, 0), _t(0, 1), _t(2, 2), _c(END, 1, 3, telemetry_count=3)]
    )

    with pytest.raises(SequenceIntegrityError, match="gap"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


# ---------------------------------------------------------------------
# Completeness: RUN_END's declared telemetry_count against what was observed
# ---------------------------------------------------------------------


def _run_with_count(declared: int, telemetry: int, **kw):
    return [
        _c(START, 0, 0),
        *[_t(i, 1 + i) for i in range(telemetry)],
        _c(END, 1, 1 + telemetry, telemetry_count=declared, **kw),
    ]


def test_run_end_count_matching_the_observed_telemetry_finalizes(
    tmp_path, install
) -> None:
    install(_run_with_count(declared=4, telemetry=4))

    result = _capture(tmp_path)

    assert result.message_count == 4


def test_missing_tail_telemetry_is_rejected_by_the_declared_count(
    tmp_path, install
) -> None:
    """Telemetry 0..999 then RUN_END(2000): nothing is out of order, only the
    count shows 1000 records never arrived."""
    created = install(_run_with_count(declared=2000, telemetry=1000))

    with pytest.raises(
        RunIncompleteError, match=r"declares 2000 .* observed 1000: 1000 missing"
    ):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])
    assert partial_bag_path(tmp_path, "run-1").exists()


def test_run_end_declaring_less_than_observed_is_rejected(tmp_path, install) -> None:
    created = install(_run_with_count(declared=2, telemetry=4))

    with pytest.raises(RunIncompleteError, match="2 more than the producer assigned"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_incomplete_error_is_a_lifecycle_error() -> None:
    assert issubclass(RunIncompleteError, RunLifecycleError)


def test_zero_telemetry_run_declaring_telemetry_is_rejected(tmp_path, install) -> None:
    created = install(_run_with_count(declared=3, telemetry=0))

    with pytest.raises(RunIncompleteError, match="declares 3 .* observed 0"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_zero_telemetry_run_with_matching_count_still_has_no_recording(
    tmp_path, install
) -> None:
    """Completeness holds (0 == 0); the empty recording is refused by the MCAP
    validation, not by the closing contract."""
    created = install(_run_with_count(declared=0, telemetry=0))

    with pytest.raises(McapValidationError, match="zero messages"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


@pytest.mark.parametrize("payload", [b"{}", b"not json", b'{"telemetry_count": "2"}'])
def test_run_end_without_a_valid_count_is_rejected(tmp_path, install, payload) -> None:
    queue = _run_with_count(declared=2, telemetry=2)
    queue[-1] = dataclasses.replace(
        queue[-1], envelope=queue[-1].envelope.model_copy(update={"payload": payload})
    )
    created = install(queue)

    with pytest.raises(RunLifecycleError, match="RUN_END (payload|must declare)"):
        _capture(tmp_path)

    _assert_nothing_finalized(tmp_path, created[0])


def test_exact_duplicate_telemetry_does_not_inflate_the_count(
    tmp_path, install
) -> None:
    queue = _run_with_count(declared=3, telemetry=3)
    queue.insert(3, queue[2])  # redelivery of telemetry sequence 1
    install(queue)

    result = _capture(tmp_path)

    assert result.message_count == 3


def test_conflicting_run_end_after_the_first_is_never_read(tmp_path, install) -> None:
    good = _run_with_count(declared=2, telemetry=2)
    stale = _c(END, 1, 4, telemetry_count=99)
    created = install([*good, stale])

    result = _capture(tmp_path)

    assert result.message_count == 2
    assert created[0].remaining == 1


def test_rejected_incomplete_run_is_rejected_again_on_replay(tmp_path, install) -> None:
    queue = _run_with_count(declared=9, telemetry=3)
    for _ in range(2):
        created = install(queue)
        with pytest.raises(RunIncompleteError):
            _capture(tmp_path)
        _assert_nothing_finalized(tmp_path, created[-1])


def test_retry_after_a_mid_run_crash_rebuilds_the_whole_run(tmp_path, install) -> None:
    install(_normal_run()[:3], tick=1.0)
    with pytest.raises(RunEndNotObservedError):
        _capture(tmp_path, idle_timeout_seconds=5)
    assert partial_bag_path(tmp_path, "run-1").exists()

    install(_normal_run())
    result = _capture(tmp_path)

    assert result.message_count == 2
    assert not partial_bag_path(tmp_path, "run-1").exists()


def test_crash_right_after_finalize_converges_on_retry(
    tmp_path, install, monkeypatch
) -> None:
    install(_normal_run())
    real_finalize_bag = capture_consumer.finalize_bag

    def finalize_then_die(output_root, robot_run_id):
        real_finalize_bag(output_root, robot_run_id)
        raise _ProcessDied

    monkeypatch.setattr(capture_consumer, "finalize_bag", finalize_then_die)
    with pytest.raises(_ProcessDied):
        _capture(tmp_path)
    monkeypatch.setattr(capture_consumer, "finalize_bag", real_finalize_bag)
    assert final_bag_path(tmp_path, "run-1").exists()
    first_bytes = next(final_bag_path(tmp_path, "run-1").glob("*.mcap")).read_bytes()

    install(_normal_run())
    result = _capture(tmp_path)
    assert result.path.read_bytes() == first_bytes


def test_retry_after_finalize_with_different_content_fails_loudly(
    tmp_path, install
) -> None:
    install(_normal_run())
    first = _capture(tmp_path)

    changed = _normal_run()
    changed[1] = _t(0, 1, payload=b"\x00\x01\x00\x00" + b"y" * 16)
    install(changed)
    with pytest.raises(FinalBagExistsError):
        _capture(tmp_path)
    assert first.path.exists()


def test_rerun_starting_past_the_run_end_is_bounded_not_endless(
    tmp_path, install
) -> None:
    """A rerun whose reading starts after the finalized run's RUN_END sees none
    of the run: with a guard the capture fails, the finalized bag is untouched."""
    install(_normal_run())
    first = _capture(tmp_path)
    before = first.path.read_bytes()

    install([], tick=1.0)
    with pytest.raises(RunStartNotObservedError):
        _capture(tmp_path, idle_timeout_seconds=30)
    assert first.path.read_bytes() == before


# ---------------------------------------------------------------------
# idle / progress semantics
# ---------------------------------------------------------------------


def test_history_scan_of_other_runs_is_progress_not_idleness(tmp_path, install) -> None:
    """60 foreign records at 1 s each against a 10 s guard: the target run
    produced nothing for a minute, but the consumer was advancing."""
    foreign = [_t(i, i, run_id="other") for i in range(60)]
    install([*foreign, *_normal_run(first_offset=60)], tick=1.0)

    result = _capture(tmp_path, idle_timeout_seconds=10)

    assert result.message_count == 2


def test_quiet_topic_before_run_start_aborts_with_run_start_not_observed(
    tmp_path, install
) -> None:
    created = install([], tick=1.0)

    with pytest.raises(RunStartNotObservedError):
        _capture(tmp_path, idle_timeout_seconds=10)

    _assert_nothing_finalized(tmp_path, created[0])


def test_other_runs_traffic_does_not_keep_a_dead_run_alive(tmp_path, install) -> None:
    """RUN_START and one record arrive, the bridge dies, other runs keep
    publishing: only the target run's own records are its progress."""
    queue = [
        _c(START, 0, 0),
        _t(0, 1),
        *[_t(i, 2 + i, run_id="other") for i in range(100)],
    ]
    created = install(queue, tick=1.0)

    with pytest.raises(RunEndNotObservedError):
        _capture(tmp_path, idle_timeout_seconds=10)

    _assert_nothing_finalized(tmp_path, created[0])
    assert created[0].remaining > 80  # aborted well before draining the traffic


def test_a_slow_but_live_run_is_never_idle(tmp_path, install) -> None:
    """One record every 5 s against a 10 s guard, for 50 s."""
    queue = [
        _c(START, 0, 0),
        *[_t(i, 1 + i) for i in range(10)],
        _c(END, 1, 11, telemetry_count=10),
    ]
    install(queue, tick=5.0)

    result = _capture(tmp_path, idle_timeout_seconds=10)

    assert result.message_count == 10


def test_idle_timeout_requires_a_lifecycle_governed_capture(tmp_path, install) -> None:
    install([])
    with pytest.raises(ValueError, match="idle_timeout_seconds requires"):
        _capture(tmp_path, stop_on_run_end=False, idle_timeout_seconds=5)
    with pytest.raises(ValueError, match="needs a stop_condition"):
        _capture(tmp_path, stop_on_run_end=False)
