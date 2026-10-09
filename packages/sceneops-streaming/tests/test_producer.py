from __future__ import annotations

import asyncio

import pytest
from confluent_kafka import KafkaError, KafkaException

from sceneops_streaming import StreamingSettings, TelemetryEnvelope
from sceneops_streaming import producer as producer_module
from sceneops_streaming.envelope import EnvelopeEncoding
from sceneops_streaming.producer import KafkaTelemetryProducer


class FakeConfluentProducer:
    """Records the librdkafka config and settles every produced record
    through its delivery callback on ``flush``."""

    instances: list[FakeConfluentProducer] = []

    def __init__(self, config: dict) -> None:
        self.config = config
        self.pending: list = []
        self.fail_with: KafkaError | None = None
        self.unflushed_remaining = 0
        FakeConfluentProducer.instances.append(self)

    def produce(self, *, topic, key, value, headers, on_delivery) -> None:
        self.pending.append(on_delivery)

    def poll(self, _timeout: float) -> int:
        return 0

    def flush(self, _timeout: float) -> int:
        if self.unflushed_remaining:
            return self.unflushed_remaining
        for on_delivery in self.pending:
            on_delivery(self.fail_with, None)
        self.pending.clear()
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
