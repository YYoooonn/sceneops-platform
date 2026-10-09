"""``run_capture`` pinned to one partition (``start=KafkaStartPosition``) and
exclusively owning its run, with a fake in-process Kafka consumer (no broker) but
the real MCAP writer, finalize and ownership lock. Real-broker behaviour of the
same contract is in test_pinned_start_integration.py.

Runs only inside the capture image (needs the ROS 2 interface definitions + mcap).
"""

from __future__ import annotations

import asyncio

import pytest
from sceneops_streaming import (
    ConsumedTelemetryEnvelope,
    EnvelopeEncoding,
    KafkaStartPosition,
    RunEventType,
    StartPositionError,
    StreamingSettings,
    TelemetryEnvelope,
    build_control_envelope,
)

import sceneops_recording.capture.consumer as capture_consumer
from sceneops_recording.capture.consumer import (
    PartitionInvariantError,
    RunStartNotObservedError,
    run_capture,
)
from sceneops_recording.capture.finalize import final_bag_path, partial_bag_path
from sceneops_recording.capture.lifecycle import RunLifecycleError
from sceneops_recording.capture.ownership import RunAlreadyOwnedError, own_run

TOPIC = "sceneops.robot.telemetry.v1"
SETTINGS = StreamingSettings(telemetry_topic=TOPIC)
RUN = "run-1"
ROBOT = "robot-1"


def _telemetry(seq: int, *, run: str = RUN, partition: int = 1, offset: int = 0):
    envelope = TelemetryEnvelope(
        robot_id=ROBOT,
        robot_run_id=run,
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_000_000_000 + seq,
        ingest_timestamp_ns=2_000_000_000 + seq,
        sequence_number=seq,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=bytes([seq]) * 8 + b"\x00\x01\x00\x00",
    )
    return ConsumedTelemetryEnvelope(
        envelope=envelope,
        key=run.encode(),
        topic=TOPIC,
        partition=partition,
        offset=offset,
    )


def _control(
    event: RunEventType,
    *,
    run: str = RUN,
    seq: int,
    offset: int,
    partition: int = 1,
    telemetry_count: int | None = None,
):
    envelope = build_control_envelope(
        event_type=event,
        robot_id=ROBOT,
        robot_run_id=run,
        sequence_number=seq,
        telemetry_count=telemetry_count,
    )
    return ConsumedTelemetryEnvelope(
        envelope=envelope,
        key=run.encode(),
        topic=TOPIC,
        partition=partition,
        offset=offset,
    )


def _session(first_offset: int, telemetry: int = 3, *, partition: int = 1):
    """RUN_START, ``telemetry`` records, RUN_END -- consecutive offsets."""
    records = [
        _control(
            RunEventType.RUN_START, seq=0, offset=first_offset, partition=partition
        )
    ]
    for seq in range(telemetry):
        records.append(
            _telemetry(seq, partition=partition, offset=first_offset + 1 + seq)
        )
    records.append(
        _control(
            RunEventType.RUN_END,
            seq=1,
            offset=first_offset + 1 + telemetry,
            partition=partition,
            telemetry_count=telemetry,
        )
    )
    return records


class _FakeConsumer:
    def __init__(self, queue, end_offset) -> None:
        self._queue = list(queue)
        self.end_offset = end_offset
        self.closed = False

    async def poll(self, timeout_seconds: float):
        return self._queue.pop(0) if self._queue else None

    async def close(self) -> None:
        self.closed = True


def _install(monkeypatch, queue, *, end_offset=None, fail=None):
    created: list[_FakeConsumer] = []
    kwargs_seen: list[dict] = []

    def factory(**kwargs):
        kwargs_seen.append(kwargs)
        if fail is not None:
            raise fail
        consumer = _FakeConsumer(queue, end_offset)
        created.append(consumer)
        return consumer

    monkeypatch.setattr(capture_consumer, "KafkaTelemetryConsumer", factory)
    return created, kwargs_seen


def _capture(tmp_path, start, **kwargs):
    return asyncio.run(
        run_capture(
            settings=SETTINGS,
            robot_id=ROBOT,
            robot_run_id=RUN,
            output_root=tmp_path,
            stop_on_run_end=True,
            idle_timeout_seconds=30.0,
            start=start,
            **kwargs,
        )
    )


def test_pinned_capture_assigns_instead_of_scanning_and_finalizes(
    tmp_path, monkeypatch
) -> None:
    start = KafkaStartPosition(TOPIC, 1, 40)
    created, kwargs_seen = _install(monkeypatch, _session(40), end_offset=46)

    result = _capture(tmp_path, start)

    [kwargs] = kwargs_seen
    assert kwargs["start_position"] == start
    assert kwargs["start_from_beginning"] is False
    # Capture names no consumer group and asks for no commit behaviour: it only
    # says where to read.
    assert set(kwargs) == {"settings", "start_position", "start_from_beginning"}
    assert result.partition == 1
    assert (result.first_offset, result.last_offset) == (40, 44)
    assert result.message_count == 3
    assert created[0].closed
    assert final_bag_path(tmp_path, RUN).is_dir()


def test_without_a_start_position_the_whole_topic_is_scanned(
    tmp_path, monkeypatch
) -> None:
    created, kwargs_seen = _install(monkeypatch, _session(0, partition=0))

    result = _capture(tmp_path, None)

    [kwargs] = kwargs_seen
    assert kwargs["start_position"] is None
    assert kwargs["start_from_beginning"] is True
    assert result.partition == 0


def test_start_position_of_another_topic_is_refused_before_anything_happens(
    tmp_path, monkeypatch
) -> None:
    created, kwargs_seen = _install(monkeypatch, [])

    with pytest.raises(ValueError, match="telemetry topic"):
        _capture(tmp_path, KafkaStartPosition("other.topic", 0, 0))

    assert kwargs_seen == []
    assert not (tmp_path / ".locks").exists()


def test_a_record_of_the_run_from_another_partition_fails_loudly(
    tmp_path, monkeypatch
) -> None:
    queue = _session(40)
    queue[2] = _telemetry(1, partition=2, offset=42)
    _install(monkeypatch, queue, end_offset=46)

    with pytest.raises(PartitionInvariantError):
        _capture(tmp_path, KafkaStartPosition(TOPIC, 1, 40))

    assert not final_bag_path(tmp_path, RUN).exists()


def test_start_before_run_start_skips_other_runs_up_to_it(
    tmp_path, monkeypatch
) -> None:
    other = [_telemetry(i, run="run-other", offset=30 + i) for i in range(5)]
    created, _ = _install(monkeypatch, other + _session(35), end_offset=41)

    result = _capture(tmp_path, KafkaStartPosition(TOPIC, 1, 30))

    assert result.first_offset == 35
    assert result.message_count == 3


def test_start_after_run_start_fails_at_the_first_telemetry_record(
    tmp_path, monkeypatch
) -> None:
    created, _ = _install(monkeypatch, _session(40)[1:], end_offset=46)

    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        _capture(tmp_path, KafkaStartPosition(TOPIC, 1, 41))

    assert not final_bag_path(tmp_path, RUN).exists()


def test_start_after_the_whole_run_is_not_waited_out(tmp_path, monkeypatch) -> None:
    other = [_telemetry(i, run="run-other", offset=50 + i) for i in range(3)]
    created, _ = _install(monkeypatch, other, end_offset=53)

    with pytest.raises(RunStartNotObservedError, match=r"offsets 50\.\.52"):
        _capture(tmp_path, KafkaStartPosition(TOPIC, 1, 50))

    assert not final_bag_path(tmp_path, RUN).exists()


def test_unusable_start_position_leaves_files_untouched_and_releases_the_run(
    tmp_path, monkeypatch
) -> None:
    stale = partial_bag_path(tmp_path, RUN)
    stale.mkdir(parents=True)
    (stale / "marker").write_text("previous attempt")
    _install(monkeypatch, [], fail=StartPositionError("offset 9 below low watermark"))

    with pytest.raises(StartPositionError, match="low watermark"):
        _capture(tmp_path, KafkaStartPosition(TOPIC, 1, 9))

    assert (stale / "marker").read_text() == "previous attempt"
    with own_run(tmp_path, RUN):  # the failed attempt does not keep the run
        pass


def test_a_second_capture_of_an_owned_run_fails_before_touching_anything(
    tmp_path, monkeypatch
) -> None:
    live = partial_bag_path(tmp_path, RUN)
    live.mkdir(parents=True)
    (live / "live.mcap").write_text("being written by the owner")
    created, kwargs_seen = _install(monkeypatch, _session(0, partition=0))

    with own_run(tmp_path, RUN):
        with pytest.raises(RunAlreadyOwnedError):
            _capture(tmp_path, None)

    assert kwargs_seen == []
    assert (live / "live.mcap").read_text() == "being written by the owner"


def test_the_run_is_owned_during_the_capture_and_free_after_it(
    tmp_path, monkeypatch
) -> None:
    owned_while_polling: list[bool] = []

    class _Probe(_FakeConsumer):
        async def poll(self, timeout_seconds: float):
            try:
                with own_run(tmp_path, RUN):
                    owned_while_polling.append(False)
            except RunAlreadyOwnedError:
                owned_while_polling.append(True)
            return await super().poll(timeout_seconds)

    monkeypatch.setattr(
        capture_consumer,
        "KafkaTelemetryConsumer",
        lambda **_: _Probe(_session(0, partition=0), None),
    )

    _capture(tmp_path, None)

    assert owned_while_polling and all(owned_while_polling)
    with own_run(tmp_path, RUN):
        pass


def test_a_finalized_run_is_recaptured_to_the_same_bag(tmp_path, monkeypatch) -> None:
    start = KafkaStartPosition(TOPIC, 1, 40)
    _install(monkeypatch, _session(40), end_offset=46)
    first = _capture(tmp_path, start)

    created, _ = _install(monkeypatch, _session(40), end_offset=46)
    second = _capture(tmp_path, start)

    assert second.path == first.path
    assert second.sha256 == first.sha256
    assert not partial_bag_path(tmp_path, RUN).exists()
