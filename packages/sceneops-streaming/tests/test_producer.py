from __future__ import annotations

import asyncio

import pytest
from confluent_kafka import KafkaError, KafkaException

from sceneops_streaming import (
    DeliveredRecord,
    DeliveryError,
    StreamingSettings,
    TelemetryEnvelope,
)
from sceneops_streaming import producer as producer_module
from sceneops_streaming.envelope import EnvelopeEncoding
from sceneops_streaming.producer import KafkaTelemetryProducer


class FakeMessage:
    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self._position = (topic, partition, offset)

    def topic(self) -> str:
        return self._position[0]

    def partition(self) -> int:
        return self._position[1]

    def offset(self) -> int:
        return self._position[2]


class FakeConfluentProducer:
    """Records the librdkafka config and settles every produced record
    through its delivery callback on ``flush``."""

    instances: list[FakeConfluentProducer] = []

    def __init__(self, config: dict) -> None:
        self.config = config
        self.pending: list = []
        self.fail_with: KafkaError | None = None
        self.unflushed_remaining = 0
        # Broker behaviour for publish_and_wait: acknowledge on the n-th poll
        # (None: never), at this partition and a counter-assigned offset.
        self.ack_on_poll: int | None = 1
        self.polls = 0
        self.partition = 2
        self.next_offset = 100
        FakeConfluentProducer.instances.append(self)

    def produce(self, *, topic, key, value, headers, on_delivery) -> None:
        self.pending.append((topic, on_delivery))

    def poll(self, _timeout: float) -> int:
        self.polls += 1
        if self.ack_on_poll is not None and self.polls >= self.ack_on_poll:
            self._settle(error=self.fail_with)
        return 0

    def _settle(self, *, error) -> None:
        pending, self.pending = self.pending, []
        for topic, on_delivery in pending:
            message = FakeMessage(topic, self.partition, self.next_offset)
            self.next_offset += 1
            on_delivery(error, message)

    def flush(self, _timeout: float) -> int:
        if self.unflushed_remaining:
            return self.unflushed_remaining
        self._settle(error=self.fail_with)
        return 0


@pytest.fixture
def fake_producer(monkeypatch) -> FakeConfluentProducer:
    FakeConfluentProducer.instances.clear()
    monkeypatch.setattr(producer_module, "ConfluentProducer", FakeConfluentProducer)
    kafka_producer = KafkaTelemetryProducer(
        settings=StreamingSettings(bootstrap_servers="unused:9092"), topic="t"
    )
    kafka_producer.fake = FakeConfluentProducer.instances[-1]  # type: ignore[attr-defined]
    return kafka_producer  # type: ignore[return-value]


def _envelope(sequence_number: int = 0) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        robot_id="robot",
        robot_run_id="run-1",
        channel="/c",
        message_type="m",
        source_timestamp_ns=1,
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.RAW,
        payload=b"x",
    )


def test_producer_is_idempotent_and_acks_all(fake_producer):
    config = fake_producer.fake.config
    assert config["enable.idempotence"] is True
    assert config["acks"] == "all"
    # Idempotence derives retries and in-flight limits itself; setting them
    # here would only risk an incompatible combination.
    assert "max.in.flight.requests.per.connection" not in config
    assert "retries" not in config


def test_flush_succeeds_when_every_delivery_succeeds(fake_producer):
    async def scenario() -> None:
        await fake_producer.publish(_envelope(0))
        await fake_producer.publish(_envelope(1))
        await fake_producer.flush()

    asyncio.run(scenario())


def test_flush_raises_on_delivery_failure(fake_producer):
    fake_producer.fake.fail_with = KafkaError(KafkaError._MSG_TIMED_OUT)

    async def scenario() -> None:
        await fake_producer.publish(_envelope())
        with pytest.raises(KafkaException):
            await fake_producer.flush()

    asyncio.run(scenario())


def test_flush_raises_timeout_when_records_remain_queued(fake_producer):
    fake_producer.fake.unflushed_remaining = 3

    async def scenario() -> None:
        await fake_producer.publish(_envelope())
        with pytest.raises(TimeoutError, match="3 telemetry record"):
            await fake_producer.flush(0.01)

    asyncio.run(scenario())


def test_publish_and_wait_returns_the_acknowledged_position(fake_producer):
    fake_producer.fake.partition = 5
    fake_producer.fake.next_offset = 18432

    delivered = asyncio.run(fake_producer.publish_and_wait(_envelope()))

    assert delivered == DeliveredRecord("t", 5, 18432)
    assert delivered.as_start_position().partition == 5


def test_publish_and_wait_returns_only_after_the_delivery_report(fake_producer):
    fake_producer.fake.ack_on_poll = 3

    asyncio.run(fake_producer.publish_and_wait(_envelope()))

    # produce() alone is not an acknowledgement: it kept polling for the report.
    assert fake_producer.fake.polls == 3


def test_publish_and_wait_raises_the_delivery_error_of_its_own_record(fake_producer):
    fake_producer.fake.fail_with = KafkaError(KafkaError._MSG_TIMED_OUT)

    async def scenario() -> None:
        with pytest.raises(DeliveryError):
            await fake_producer.publish_and_wait(_envelope())
        # The failure was reported to its caller; it is not left for a later flush.
        fake_producer.fake.fail_with = None
        await fake_producer.flush()

    asyncio.run(scenario())


def test_publish_and_wait_times_out_without_a_delivery_report(fake_producer):
    fake_producer.fake.ack_on_poll = None

    with pytest.raises(TimeoutError, match="no delivery report"):
        asyncio.run(fake_producer.publish_and_wait(_envelope(), timeout_seconds=0.05))


def test_publish_stays_asynchronous(fake_producer):
    fake_producer.fake.ack_on_poll = None  # the broker never answers

    # publish() hands the record to librdkafka and returns without any report.
    asyncio.run(fake_producer.publish(_envelope()))

    assert len(fake_producer.fake.pending) == 1
