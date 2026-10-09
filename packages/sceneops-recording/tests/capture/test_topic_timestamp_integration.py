"""Kafka record timestamps on a topic created with the broker's defaults, against the
REAL local Kafka broker.

Retention ages a record by its Kafka timestamp. A topic left on ``CreateTime`` would
age it by the producer's clock, so a robot clock behind the broker's by more than the
replay window would make a run's records expire at the first retention check. The
broker's default is ``LogAppendTime`` (``compose/streaming.yaml``); these tests fail if
a topic created like the bridge's auto-created one does not inherit it.

Runs only inside the ROS 2 capture image (``make streaming-test``) and needs a
running Kafka broker (``make streaming-up``).
"""

from __future__ import annotations

import time
import uuid

import pytest
from confluent_kafka import (
    TIMESTAMP_LOG_APPEND_TIME,
    Consumer,
    Producer,
    TopicPartition,
)
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic
from sceneops_streaming import EnvelopeEncoding, StreamingSettings, TelemetryEnvelope
from sceneops_streaming.wire import encode_envelope

DAY_MS = 86_400_000
SOURCE_NS = 1_700_000_000_000_000_000
INGEST_NS = 1_700_000_000_500_000_000

_BOOTSTRAP = StreamingSettings().bootstrap_servers
_ADMIN = AdminClient({"bootstrap.servers": _BOOTSTRAP})


@pytest.fixture
def topic() -> str:
    name = f"sceneops.robot.telemetry.timestamp-test.{uuid.uuid4().hex[:12]}.v1"
    # No per-topic config: the topic must inherit the broker default.
    _ADMIN.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)])[
        name
    ].result(timeout=30)
    yield name
    _ADMIN.delete_topics([name])


def _produce(topic: str, producer_clock_offset_ms: int) -> tuple[int, int]:
    """Produce 3 records stamped as a producer whose clock is offset from the
    broker's would stamp them; return (first producer wall ms, last)."""
    producer = Producer({"bootstrap.servers": _BOOTSTRAP, "enable.idempotence": True})
    wall = []
    for seq in range(3):
        envelope = TelemetryEnvelope(
            robot_id="robot-ts",
            robot_run_id="run-ts",
            channel="/vehicle/odom",
            message_type="nav_msgs/msg/Odometry",
            source_timestamp_ns=SOURCE_NS + seq,
            ingest_timestamp_ns=INGEST_NS + seq,
            sequence_number=seq,
            encoding=EnvelopeEncoding.ROS2_CDR,
            payload=bytes([seq]) * 16,
        )
        record = encode_envelope(envelope)
        wall.append(int(time.time() * 1000))
        producer.produce(
            topic=topic,
            key=record.key,
            value=record.value,
            headers=record.headers,
            timestamp=wall[-1] + producer_clock_offset_ms,
        )
        producer.flush(10)
    return wall[0], wall[-1]


def _read_all(topic: str):
    consumer = Consumer(
        {
            "bootstrap.servers": _BOOTSTRAP,
            "group.id": f"timestamp-test-{uuid.uuid4().hex[:8]}",
            "enable.auto.commit": False,
        }
    )
    consumer.assign([TopicPartition(topic, 0, 0)])
    records = []
    while len(records) < 3:
        message = consumer.poll(10)
        assert message is not None and message.error() is None
        records.append(message)
    consumer.close()
    return records


def test_a_default_created_topic_uses_log_append_time(topic):
    resource = ConfigResource(ConfigResource.Type.TOPIC, topic)
    configs = _ADMIN.describe_configs([resource])[resource].result(timeout=30)
    assert configs["message.timestamp.type"].value == "LogAppendTime"


@pytest.mark.parametrize(
    "skew_ms", [-DAY_MS, 0, DAY_MS], ids=["behind", "exact", "ahead"]
)
def test_record_age_is_the_broker_append_time_whatever_the_producer_clock(
    topic, skew_ms
):
    first_wall, last_wall = _produce(topic, skew_ms)
    records = _read_all(topic)

    for message in records:
        timestamp_type, timestamp_ms = message.timestamp()
        assert timestamp_type == TIMESTAMP_LOG_APPEND_TIME
        # The producer's stamp is discarded: the record's age starts when the
        # broker (same host clock here) appended it.
        assert first_wall - 5_000 <= timestamp_ms <= last_wall + 10_000

        # The envelope's own clocks are the producer's and travel unchanged.
        headers = dict(message.headers())
        seq = message.offset()
        assert (
            headers["sceneops.envelope.source_timestamp_ns"]
            == str(SOURCE_NS + seq).encode()
        )
        assert (
            headers["sceneops.envelope.ingest_timestamp_ns"]
            == str(INGEST_NS + seq).encode()
        )
