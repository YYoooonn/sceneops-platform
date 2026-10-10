"""``KafkaTelemetryProducer`` against a REAL broker that stops answering.

A throwaway single-node broker (the compose broker's image, its own container and
port) is frozen with ``docker pause`` and thawed again, so the producer's local
queue really fills while the broker is unreachable and really drains when it
returns. The queue is cut to a few hundred messages so no test needs a gigabyte of
data; the queue-full semantics are the same at any limit.

Not part of ``make test``: it needs Docker and the broker image, and is switched on
by ``SCENEOPS_BROKER_FAULT_TESTS=1``, which ``make streaming-producer-test`` (part of
``make test-infrastructure SUITE=kafka``) sets. A skip there is a failure.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid

import pytest
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

from sceneops_streaming import (
    ProducerQueueFullError,
    StreamingSettings,
    TelemetryEnvelope,
)
from sceneops_streaming import producer as producer_module
from sceneops_streaming.envelope import EnvelopeEncoding
from sceneops_streaming.producer import KafkaTelemetryProducer
from sceneops_streaming.wire import decode_envelope

BROKER_IMAGE = "apache/kafka:3.9.2"  # compose/streaming.yaml
QUEUE_MESSAGES = 300
RUN = "run-overload"

pytestmark = pytest.mark.skipif(
    os.environ.get("SCENEOPS_BROKER_FAULT_TESTS") != "1"
    or shutil.which("docker") is None,
    reason="needs Docker and the broker image: make streaming-producer-test",
)


class FaultableBroker:
    """One disposable KRaft broker container that can be frozen and thawed."""

    def __init__(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.name = f"sceneops-fault-broker-{uuid.uuid4().hex[:8]}"
        self.bootstrap = f"localhost:{self.port}"

    def start(self) -> None:
        env = {
            "KAFKA_NODE_ID": "1",
            "KAFKA_PROCESS_ROLES": "broker,controller",
            "KAFKA_LISTENERS": "PLAINTEXT://:9092,CONTROLLER://:9093",
            "KAFKA_ADVERTISED_LISTENERS": f"PLAINTEXT://localhost:{self.port}",
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT",
            "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
            "KAFKA_INTER_BROKER_LISTENER_NAME": "PLAINTEXT",
            "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@localhost:9093",
            "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
            "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "true",
            "KAFKA_NUM_PARTITIONS": "1",
        }
        command = [
            "docker", "run", "-d", "--rm", "--name", self.name,
            "-p", f"127.0.0.1:{self.port}:9092",
        ]  # fmt: skip
        for key, value in env.items():
            command += ["-e", f"{key}={value}"]
        subprocess.run(command + [BROKER_IMAGE], check=True, capture_output=True)
        admin = AdminClient({"bootstrap.servers": self.bootstrap})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                admin.list_topics(timeout=2)
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError(f"broker {self.name} did not become ready")

    def freeze(self) -> None:
        subprocess.run(["docker", "pause", self.name], check=True, capture_output=True)

    def thaw(self) -> None:
        subprocess.run(["docker", "unpause", self.name], capture_output=True)

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


@pytest.fixture(scope="module")
def broker():
    instance = FaultableBroker()
    try:
        instance.start()
        yield instance
    finally:
        instance.stop()


@pytest.fixture
def topic(broker) -> str:
    broker.thaw()  # a failed test must not leave the next one a frozen broker
    return f"sceneops.overload.{uuid.uuid4().hex[:10]}"


def _make_producer(
    monkeypatch, broker, topic: str, *, queue_full_timeout_seconds: float
) -> KafkaTelemetryProducer:
    real = producer_module.ConfluentProducer
    monkeypatch.setattr(
        producer_module,
        "ConfluentProducer",
        lambda config: real({**config, "queue.buffering.max.messages": QUEUE_MESSAGES}),
    )
    return KafkaTelemetryProducer(
        settings=StreamingSettings(bootstrap_servers=broker.bootstrap),
        topic=topic,
        queue_full_timeout_seconds=queue_full_timeout_seconds,
    )


def _envelope(sequence_number: int) -> TelemetryEnvelope:
    return TelemetryEnvelope(
        robot_id="robot",
        robot_run_id=RUN,
        channel="/c",
        message_type="m",
        source_timestamp_ns=1,
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.RAW,
        payload=b"x" * 100,
    )


def _offer(producer: KafkaTelemetryProducer, sequence_number: int) -> bool:
    """True if the producer accepted the record, False if it refused it."""
    try:
        asyncio.run(producer.publish(_envelope(sequence_number)))
        return True
    except ProducerQueueFullError:
        return False


def _warm_up(producer: KafkaTelemetryProducer) -> None:
    """Idempotent producers obtain their id on first contact; do that while the
    broker still answers."""
    asyncio.run(producer.publish_and_wait(_envelope(0)))


def _sequences_on_broker(broker, topic: str) -> list[int]:
    consumer = Consumer(
        {
            "bootstrap.servers": broker.bootstrap,
            "group.id": uuid.uuid4().hex,
            "enable.auto.commit": False,
            "enable.partition.eof": True,
        }
    )
    consumer.assign([TopicPartition(topic, 0, 0)])
    sequences: list[int] = []
    try:
        while True:
            message = consumer.poll(10)
            if message is None:
                raise TimeoutError(
                    f"no end of partition after {len(sequences)} records"
                )
            if message.error():
                if message.error().code() == -191:  # _PARTITION_EOF
                    return sequences
                raise RuntimeError(message.error())
            sequences.append(
                decode_envelope(
                    headers=message.headers(), value=message.value()
                ).sequence_number
            )
    finally:
        consumer.close()


def test_a_queue_filled_during_an_outage_drains_and_publishing_resumes(
    monkeypatch, broker, topic
):
    producer = _make_producer(
        monkeypatch, broker, topic, queue_full_timeout_seconds=0.05
    )
    _warm_up(producer)
    accepted, rejected = [0], []
    sequence = 1

    broker.freeze()
    while len(rejected) < 20:  # the queue fills, then every record is refused
        (accepted if _offer(producer, sequence) else rejected).append(sequence)
        sequence += 1
    assert len(accepted) - 1 >= QUEUE_MESSAGES - 1  # it held a full queue's worth
    broker.thaw()

    # Recovery: the broker answers again, so a later publish must be accepted
    # within a bounded time -- no restart, no flush needed to unwedge it.
    recovery_started = time.monotonic()
    while not _offer(producer, sequence):
        rejected.append(sequence)
        sequence += 1
        assert time.monotonic() - recovery_started < 30, "producer is wedged"
    accepted.append(sequence)
    sequence += 1
    for _ in range(100):
        assert _offer(producer, sequence), "a recovered producer refused a record"
        accepted.append(sequence)
        sequence += 1

    # flush() speaks only for accepted records: it succeeds although
    # records were refused.
    asyncio.run(producer.flush(30))
    assert rejected

    on_broker = _sequences_on_broker(broker, topic)
    assert on_broker == accepted  # every accepted record, once, in order
    assert not set(rejected) & set(on_broker)  # refused records left holes


def test_an_outage_shorter_than_the_wait_loses_nothing(monkeypatch, broker, topic):
    producer = _make_producer(monkeypatch, broker, topic, queue_full_timeout_seconds=30)
    _warm_up(producer)
    accepted = [0]

    broker.freeze()
    for sequence in range(1, QUEUE_MESSAGES):  # fill the queue without blocking
        assert _offer(producer, sequence)
        accepted.append(sequence)
    thaw = threading.Timer(1.5, broker.thaw)
    thaw.start()
    started = time.monotonic()
    try:
        # The queue is full: this publish waits for the broker, then succeeds.
        while len(accepted) < QUEUE_MESSAGES + 50:
            sequence = len(accepted)
            assert _offer(producer, sequence), "a record was refused during the wait"
            accepted.append(sequence)
    finally:
        thaw.join()
    assert time.monotonic() - started >= 1.0  # it did have to wait for the broker

    asyncio.run(producer.flush(30))
    assert _sequences_on_broker(broker, topic) == accepted


def test_waiting_on_a_full_queue_does_not_spin_and_is_paid_once(
    monkeypatch, broker, topic
):
    producer = _make_producer(
        monkeypatch, broker, topic, queue_full_timeout_seconds=2.0
    )
    _warm_up(producer)
    broker.freeze()
    try:
        sequence = 1
        while True:  # fill the queue; the first refusal is the full-queue wait
            started, cpu_started = time.monotonic(), time.process_time()
            if not _offer(producer, sequence):
                break
            sequence += 1
        waited = time.monotonic() - started
        cpu = time.process_time() - cpu_started
        # The queue stays full: the following records are refused at once, not
        # one wait each.
        started = time.monotonic()
        assert not any(_offer(producer, sequence + n) for n in range(1, 51))
        refused_for = time.monotonic() - started
    finally:
        broker.thaw()
    assert 2.0 <= waited < 4.0
    # Retrying produce() in a loop would burn a core for the whole wait (~2 s of
    # CPU); librdkafka's own threads cost ~0.4 s against a frozen broker either way.
    assert cpu < 1.0, f"{cpu:.2f}s CPU in a {waited:.2f}s wait"
    assert refused_for < 1.0, f"50 refusals took {refused_for:.2f}s"
    asyncio.run(producer.flush(30))


def test_a_broker_that_never_returns_is_reported_not_hidden(monkeypatch, broker, topic):
    producer = _make_producer(
        monkeypatch, broker, topic, queue_full_timeout_seconds=0.05
    )
    _warm_up(producer)
    broker.freeze()
    try:
        refused = 0
        for sequence in range(1, QUEUE_MESSAGES + 20):
            refused += not _offer(producer, sequence)
        assert refused > 0
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="still undelivered"):
            asyncio.run(producer.flush(1.0))
        assert time.monotonic() - started < 5.0
    finally:
        broker.thaw()
