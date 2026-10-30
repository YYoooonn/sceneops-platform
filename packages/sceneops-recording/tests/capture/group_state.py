"""Broker-side check shared by the real-Kafka capture tests: a Capture attempt
leaves no consumer-group state behind."""

from __future__ import annotations

from confluent_kafka import OFFSET_INVALID, Consumer, TopicPartition
from confluent_kafka.admin import AdminClient
from sceneops_streaming import StreamingSettings
from sceneops_streaming.consumer import ASSIGNED_READER_GROUP_ID


def assert_leaves_no_group_state(topic: str, partition: int, run_id: str) -> None:
    """Capture reads Kafka and records no progress in it: nothing is committed
    for the partition under the one group id its consumer is configured with, and
    no consumer group exists for the run."""
    bootstrap = StreamingSettings().bootstrap_servers
    probe = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": ASSIGNED_READER_GROUP_ID,
            "enable.auto.commit": False,
        }
    )
    try:
        [tp] = probe.committed([TopicPartition(topic, partition)], timeout=10)
    finally:
        probe.close()
    assert tp.offset == OFFSET_INVALID
    groups = {
        g.group_id
        for g in AdminClient({"bootstrap.servers": bootstrap})
        .list_consumer_groups()
        .result()
        .valid
    }
    assert ASSIGNED_READER_GROUP_ID not in groups
    assert not [g for g in groups if run_id in g]
