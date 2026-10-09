"""A pinned Capture (``run_capture(start=KafkaStartPosition(...))``) against the
REAL local Kafka broker, on topics with several partitions.

The start positions come from the producer's delivery reports -- the source a
launcher would use: the (topic, partition, offset) librdkafka reports for the
RUN_START record once the broker has acknowledged it.

Runs only inside the ROS 2 capture image (``make streaming-test``) and needs a
running Kafka broker (``make streaming-up``).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

import pytest
from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from group_state import assert_leaves_no_group_state
from sceneops_streaming import (
    EnvelopeEncoding,
    KafkaStartPosition,
    RunEventType,
    StartPositionError,
    StreamingSettings,
    TelemetryEnvelope,
    build_control_envelope,
)
from sceneops_streaming.consumer import ASSIGNED_READER_GROUP_ID
from sceneops_streaming.wire import encode_envelope

from sceneops_recording.capture.consumer import (
    RunEndNotObservedError,
    RunStartNotObservedError,
    run_capture,
)
from sceneops_recording.capture.finalize import final_bag_path, partial_bag_path
from sceneops_recording.capture.lifecycle import RunLifecycleError
from sceneops_recording.capture.receipt_io import read_capture_receipt

PARTITIONS = 3


# One long-lived client: a futures-returning call on a client that is garbage
# collected before its future resolves fails with "Handle is terminating".
_ADMIN = AdminClient({"bootstrap.servers": StreamingSettings().bootstrap_servers})


def _create_topic(name: str) -> None:
    _ADMIN.create_topics(
        [NewTopic(name, num_partitions=PARTITIONS, replication_factor=1)]
    )[name].result(timeout=30)


@pytest.fixture
def topic() -> str:
    name = f"sceneops.robot.telemetry.pinned-test.{uuid.uuid4().hex[:12]}.v1"
    _create_topic(name)
    yield name
    _ADMIN.delete_topics([name])


@dataclass
class Delivered:
    topic: str
    partition: int
    offset: int

    @property
    def start(self) -> KafkaStartPosition:
        return KafkaStartPosition(self.topic, self.partition, self.offset)


class Publisher:
    """Publishes like ``KafkaTelemetryProducer`` (same producer settings, same
    wire encoding) and keeps what each delivery report says about where the
    record landed."""

    def __init__(self, topic: str) -> None:
        self.topic = topic
        self.delivered: dict[tuple[str, str, int], Delivered] = {}
        # Run ids on the same partition as a given run (set by the scenario).
        self.spare: dict[str, list[str]] = {}
        self._producer = Producer(
            {
                "bootstrap.servers": StreamingSettings().bootstrap_servers,
                "enable.idempotence": True,
                "acks": "all",
            }
        )

    def _send(self, key: tuple[str, str, int], envelope: TelemetryEnvelope) -> None:
        record = encode_envelope(envelope)

        def on_delivery(err, msg) -> None:
            assert err is None, err
            self.delivered[key] = Delivered(msg.topic(), msg.partition(), msg.offset())

        self._producer.produce(
            topic=self.topic,
            key=record.key,
            value=record.value,
            headers=record.headers,
            on_delivery=on_delivery,
        )

    def run_start(self, run: str) -> None:
        self._send(
            (run, "start", 0),
            build_control_envelope(
                event_type=RunEventType.RUN_START,
                robot_id=f"robot-{run}",
                robot_run_id=run,
            ),
        )

    def telemetry(self, run: str, seq: int) -> None:
        self._send(
            (run, "telemetry", seq),
            TelemetryEnvelope(
                robot_id=f"robot-{run}",
                robot_run_id=run,
                channel="/vehicle/odom",
                message_type="nav_msgs/msg/Odometry",
                source_timestamp_ns=1_700_000_000_000_000_000 + seq,
                ingest_timestamp_ns=1_700_000_000_500_000_000 + seq,
                sequence_number=seq,
                encoding=EnvelopeEncoding.ROS2_CDR,
                payload=bytes([0x00, 0x01, 0x00, 0x00]) + bytes([seq % 256]) * 16,
            ),
        )

    def run_end(self, run: str, telemetry_count: int) -> None:
        self._send(
            (run, "end", 1),
            build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id=f"robot-{run}",
                robot_run_id=run,
                sequence_number=1,
                telemetry_count=telemetry_count,
            ),
        )

    def flush(self) -> None:
        assert self._producer.flush(30) == 0

    def start_of(self, run: str) -> Delivered:
        return self.delivered[(run, "start", 0)]

    def end_of(self, run: str) -> Delivered:
        return self.delivered[(run, "end", 1)]


def _run_ids_per_partition(topic: str, per_partition: int) -> list[list[str]]:
    """For each partition, ``per_partition`` run ids that Kafka keys onto it
    (probed on a scratch topic, so the test topic's offsets stay untouched)."""
    scratch = f"{topic}.probe"
    _create_topic(scratch)
    try:
        probe = Publisher(scratch)
        candidates = [f"probe-{uuid.uuid4().hex[:8]}-{i}" for i in range(300)]
        for run in candidates:
            probe.run_start(run)
        probe.flush()
        by_partition: dict[int, list[str]] = {}
        for run in candidates:
            by_partition.setdefault(probe.start_of(run).partition, []).append(run)
        assert len(by_partition) == PARTITIONS
        assert all(len(ids) >= per_partition for ids in by_partition.values())
        return [ids[:per_partition] for _, ids in sorted(by_partition.items())]
    finally:
        _ADMIN.delete_topics([scratch])


def _capture(tmp_path, topic, run, start, **kwargs):
    return asyncio.run(
        run_capture(
            settings=StreamingSettings(telemetry_topic=topic),
            robot_id=f"robot-{run}",
            robot_run_id=run,
            output_root=tmp_path,
            stop_on_run_end=True,
            idle_timeout_seconds=kwargs.pop("idle_timeout_seconds", 20.0),
            poll_timeout_seconds=1.0,
            start=start,
            **kwargs,
        )
    )


def _publish_three_runs(topic):
    """Three runs on three partitions. Each partition first receives an earlier
    unrelated run's RUN_START (history), then the three sessions interleave."""
    ids = _run_ids_per_partition(topic, 6)
    runs = [partition_ids[1] for partition_ids in ids]
    pub = Publisher(topic)
    pub.spare = {run: partition_ids[2:] for run, partition_ids in zip(runs, ids)}
    for partition_ids in ids:
        pub.run_start(partition_ids[0])
    for run in runs:
        pub.run_start(run)
    for seq in range(4):
        for run in runs:
            pub.telemetry(run, seq)
    for run in runs:
        pub.run_end(run, 4)
    pub.flush()
    return runs, pub


def test_delivery_report_gives_the_position_of_each_record(topic):
    pub = Publisher(topic)
    pub.run_start("run-a")
    pub.telemetry("run-a", 0)
    pub.telemetry("run-a", 1)
    pub.run_end("run-a", 2)
    pub.flush()

    start, end = pub.start_of("run-a"), pub.end_of("run-a")
    assert start.topic == end.topic == topic
    assert 0 <= start.partition < PARTITIONS
    assert end.partition == start.partition  # one run, one partition
    assert end.offset == start.offset + 3  # RUN_START, 2 telemetry, RUN_END


def test_capture_at_the_exact_run_start_offset(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)

    for run in runs:
        start = pub.start_of(run)
        result = _capture(tmp_path, topic, run, start.start)

        assert result.partition == start.partition
        assert result.first_offset == start.offset
        assert result.last_offset == pub.end_of(run).offset
        assert result.message_count == 4
        receipt = read_capture_receipt(final_bag_path(tmp_path, run))
        assert receipt.kafka.partition == start.partition
        assert_leaves_no_group_state(topic, start.partition, run)


def test_capture_starting_before_run_start_scans_forward_to_it(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    start = pub.start_of(run)
    assert start.offset > 0  # the history record precedes it

    result = _capture(
        tmp_path, topic, run, KafkaStartPosition(topic, start.partition, 0)
    )

    assert result.first_offset == start.offset
    assert result.message_count == 4


def test_start_after_run_start_fails_loudly(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    start = pub.start_of(run)

    with pytest.raises(RunLifecycleError, match="precedes RUN_START"):
        _capture(
            tmp_path,
            topic,
            run,
            KafkaStartPosition(topic, start.partition, start.offset + 1),
        )

    assert not final_bag_path(tmp_path, run).exists()


def test_start_after_the_end_of_the_run_fails_without_waiting_for_the_idle_timeout(
    tmp_path, topic
):
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    end = pub.end_of(run)
    # Records after the run's RUN_END on its partition, so the position is in
    # the log but nothing of the run follows it.
    for later in pub.spare[run]:
        pub.run_start(later)
    pub.flush()
    high = max(d.offset for d in pub.delivered.values() if d.partition == end.partition)
    assert high > end.offset

    started = time.monotonic()
    with pytest.raises(RunStartNotObservedError, match="RUN_START of"):
        _capture(
            tmp_path,
            topic,
            run,
            KafkaStartPosition(topic, end.partition, end.offset + 1),
            idle_timeout_seconds=120.0,
        )
    assert time.monotonic() - started < 30


def test_wrong_partition_fails_loudly(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)
    run, other = runs[0], runs[1]
    elsewhere = pub.start_of(other)
    assert elsewhere.partition != pub.start_of(run).partition

    # The partition exists and holds records, just not this run's.
    with pytest.raises(RunStartNotObservedError):
        _capture(tmp_path, topic, run, elsewhere.start)

    with pytest.raises(StartPositionError, match="no partition"):
        _capture(tmp_path, topic, run, KafkaStartPosition(topic, PARTITIONS + 4, 0))

    assert not final_bag_path(tmp_path, run).exists()


def test_offset_beyond_the_log_end_fails_explicitly(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    start = pub.start_of(run)

    with pytest.raises(StartPositionError, match="not in the log"):
        _capture(
            tmp_path, topic, run, KafkaStartPosition(topic, start.partition, 10_000)
        )


def test_offset_below_the_retention_watermark_fails_explicitly(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    start = pub.start_of(run)

    # Retention advanced the log start past the run's RUN_START.
    advanced = TopicPartition(topic, start.partition, start.offset + 1)
    _ADMIN.delete_records([advanced])[advanced].result(timeout=30)

    with pytest.raises(StartPositionError, match="below the low watermark"):
        _capture(tmp_path, topic, run, start.start)

    assert not final_bag_path(tmp_path, run).exists()
    assert not partial_bag_path(tmp_path, run).exists()


def test_without_a_start_position_the_topic_is_scanned_from_the_beginning(
    tmp_path, topic
):
    runs, pub = _publish_three_runs(topic)

    for run in runs:
        result = _capture(tmp_path, topic, run, None)
        assert result.first_offset == pub.start_of(run).offset
        assert result.message_count == 4
        assert_leaves_no_group_state(topic, result.partition, run)


@pytest.mark.parametrize("pinned", [True, False])
def test_offsets_committed_in_the_readers_group_are_never_a_start_position(
    tmp_path, topic, pinned
):
    """Capture neither reads nor writes group offsets: an offset committed past
    the whole run, for the very group id its consumer is configured with, changes
    nothing -- the run is captured from the position it was given (or from the
    beginning)."""
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    start = pub.start_of(run)
    past_the_run = TopicPartition(topic, start.partition, pub.end_of(run).offset + 1)
    group = Consumer(
        {
            "bootstrap.servers": StreamingSettings().bootstrap_servers,
            "group.id": ASSIGNED_READER_GROUP_ID,
            "enable.auto.commit": False,
        }
    )
    try:
        group.commit(offsets=[past_the_run], asynchronous=False)
        result = _capture(tmp_path, topic, run, start.start if pinned else None)
    finally:
        group.close()
        _ADMIN.delete_consumer_groups([ASSIGNED_READER_GROUP_ID])[
            ASSIGNED_READER_GROUP_ID
        ].result(timeout=30)

    assert (result.first_offset, result.last_offset) == (
        start.offset,
        pub.end_of(run).offset,
    )
    assert result.message_count == 4


def test_restart_after_an_incomplete_attempt_rebuilds_from_the_same_position(
    tmp_path, topic
):
    run = "run-restart"
    pub = Publisher(topic)
    pub.run_start(run)
    for seq in range(3):
        pub.telemetry(run, seq)
    pub.flush()
    start = pub.start_of(run).start

    # The first attempt gives up before RUN_END, leaving its partial bag and
    # not finalizing.
    with pytest.raises(RunEndNotObservedError):
        _capture(tmp_path, topic, run, start, idle_timeout_seconds=3.0)
    assert partial_bag_path(tmp_path, run).is_dir()
    assert not final_bag_path(tmp_path, run).exists()

    # RUN_END arrives; the next attempt discards the stale partial and converges.
    pub.run_end(run, 3)
    pub.flush()
    result = _capture(tmp_path, topic, run, start)

    assert result.message_count == 3
    assert not partial_bag_path(tmp_path, run).exists()
    assert_leaves_no_group_state(topic, start.partition, run)


def test_retry_of_a_finalized_run_converges_on_the_same_bag(tmp_path, topic):
    runs, pub = _publish_three_runs(topic)
    run = runs[0]
    start = pub.start_of(run).start

    first = _capture(tmp_path, topic, run, start)
    second = _capture(tmp_path, topic, run, start)

    assert second.path == first.path
    assert second.sha256 == first.sha256
    assert not partial_bag_path(tmp_path, run).exists()
