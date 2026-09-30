"""Unit tests for KafkaTelemetryConsumer's manual-commit support and
(Phase 7.0.1) its internal batched-fetch/buffer behavior. No real broker
-- confluent_kafka.Consumer is monkeypatched with a minimal fake that
records constructor config, consume()/commit() calls, and serves
pre-loaded fake messages.
"""

from __future__ import annotations

import pytest

from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope
from sceneops_streaming import consumer as consumer_module
from sceneops_streaming.config import StreamingSettings
from sceneops_streaming.consumer import KafkaTelemetryConsumer
from sceneops_streaming.errors import EnvelopeDecodeError
from sceneops_streaming.wire import encode_envelope


class _FakeMessage:
    """Stands in for a real ``confluent_kafka.Message`` -- only the
    methods ``KafkaTelemetryConsumer`` actually calls."""

    def __init__(
        self, *, topic, partition, offset, key, headers, value, error=None
    ) -> None:
        self._topic = topic
        self._partition = partition
        self._offset = offset
        self._key = key
        self._headers = headers
        self._value = value
        self._error = error

    def topic(self):
        return self._topic

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def key(self):
        return self._key

    def headers(self):
        return self._headers

    def value(self):
        return self._value

    def error(self):
        return self._error


def _envelope(**overrides: object) -> TelemetryEnvelope:
    kwargs = dict(
        robot_id="robot-1",
        robot_run_id="robotrun-1",
        channel="/vehicle/imu",
        message_type="sensor_msgs/msg/Imu",
        source_timestamp_ns=1_700_000_000_000_000_000,
        ingest_timestamp_ns=1_700_000_000_250_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=bytes([0xDE, 0xAD, 0xBE, 0xEF]),
    )
    kwargs.update(overrides)
    return TelemetryEnvelope(**kwargs)


def _fake_message(
    envelope: TelemetryEnvelope, *, partition=0, offset=0
) -> _FakeMessage:
    record = encode_envelope(envelope)
    return _FakeMessage(
        topic="sceneops.robot.telemetry.v1",
        partition=partition,
        offset=offset,
        key=record.key,
        headers=record.headers,
        value=record.value,
    )


def _error_message(*, partition=0, offset=0, error="boom") -> _FakeMessage:
    return _FakeMessage(
        topic="sceneops.robot.telemetry.v1",
        partition=partition,
        offset=offset,
        key=b"",
        headers=[],
        value=b"",
        error=error,
    )


class _FakeConfluentConsumer:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.subscribed_topics: list[str] = []
        self.committed: list[tuple[object, bool]] = []
        self.closed = False
        self._queue: list[_FakeMessage] = []
        # (num_messages, timeout) for every consume() call -- lets tests
        # assert batching actually reduces the number of underlying
        # blocking fetch calls, not just that poll() still "works".
        self.consume_calls: list[tuple[int, float]] = []

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics = topics

    def enqueue(self, *messages: _FakeMessage) -> None:
        self._queue.extend(messages)

    def consume(self, num_messages: int = 1, timeout: float = -1) -> list[_FakeMessage]:
        self.consume_calls.append((num_messages, timeout))
        if not self._queue:
            return []
        batch, self._queue = self._queue[:num_messages], self._queue[num_messages:]
        return batch

    def commit(self, message=None, asynchronous=True) -> None:
        self.committed.append((message, asynchronous))

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_confluent_consumer(monkeypatch):
    monkeypatch.setattr(consumer_module, "ConfluentConsumer", _FakeConfluentConsumer)


def test_enable_auto_commit_defaults_true_unchanged_for_existing_callers() -> None:
    consumer = KafkaTelemetryConsumer(settings=StreamingSettings(_env_file=None))
    assert consumer._consumer.config["enable.auto.commit"] is True


def test_enable_auto_commit_false_is_passed_through() -> None:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(_env_file=None), enable_auto_commit=False
    )
    assert consumer._consumer.config["enable.auto.commit"] is False


async def test_commit_with_no_polled_message_is_a_no_op() -> None:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(_env_file=None), enable_auto_commit=False
    )
    await consumer.commit()
    assert consumer._consumer.committed == []


async def test_commit_commits_the_last_polled_message_synchronously() -> None:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(_env_file=None), enable_auto_commit=False
    )
    sentinel_message = object()
    consumer._last_message = sentinel_message

    await consumer.commit()

    assert consumer._consumer.committed == [(sentinel_message, False)]


# ---------------------------------------------------------------------
# Phase 7.0.1: batched-fetch / buffer behavior
# ---------------------------------------------------------------------


def _consumer(**kwargs) -> KafkaTelemetryConsumer:
    return KafkaTelemetryConsumer(settings=StreamingSettings(_env_file=None), **kwargs)


async def test_one_batch_fetch_serves_multiple_poll_calls() -> None:
    """The core Phase 7.0.1 claim: N buffered records should require
    far fewer underlying consume() calls than N poll() calls."""
    consumer = _consumer(poll_batch_size=10)
    envelopes = [_envelope(sequence_number=i) for i in range(5)]
    consumer._consumer.enqueue(
        *[_fake_message(e, offset=i) for i, e in enumerate(envelopes)]
    )

    results = [await consumer.poll(1.0) for _ in range(5)]

    assert [r.envelope.sequence_number for r in results] == [0, 1, 2, 3, 4]
    # 5 records served, but only ONE underlying blocking fetch call --
    # the whole point of batching.
    assert len(consumer._consumer.consume_calls) == 1
    assert consumer._consumer.consume_calls[0] == (10, 1.0)


async def test_bounded_buffer_refetches_once_drained() -> None:
    """poll_batch_size bounds each fetch -- draining a batch triggers
    exactly one new fetch, not an unbounded readahead."""
    consumer = _consumer(poll_batch_size=2)
    envelopes = [_envelope(sequence_number=i) for i in range(5)]
    consumer._consumer.enqueue(
        *[_fake_message(e, offset=i) for i, e in enumerate(envelopes)]
    )

    results = [await consumer.poll(1.0) for _ in range(5)]

    assert [r.envelope.sequence_number for r in results] == [0, 1, 2, 3, 4]
    # ceil(5 / 2) = 3 fetch calls: [0,1], [2,3], [4]
    assert len(consumer._consumer.consume_calls) == 3
    assert all(call == (2, 1.0) for call in consumer._consumer.consume_calls)


async def test_record_ordering_preserved_across_batches() -> None:
    consumer = _consumer(poll_batch_size=3)
    envelopes = [_envelope(sequence_number=i) for i in range(7)]
    consumer._consumer.enqueue(
        *[_fake_message(e, offset=i) for i, e in enumerate(envelopes)]
    )

    results = [await consumer.poll(1.0) for _ in range(7)]

    assert [r.envelope.sequence_number for r in results] == list(range(7))
    assert [r.offset for r in results] == list(range(7))


async def test_poll_returns_none_on_timeout_with_no_messages() -> None:
    consumer = _consumer(poll_batch_size=128)

    result = await consumer.poll(1.5)

    assert result is None
    assert consumer._consumer.consume_calls == [(128, 1.5)]


async def test_timeout_not_multiplied_by_batch_size() -> None:
    """The timeout passed to the underlying fetch is the caller's
    timeout_seconds verbatim -- never scaled by poll_batch_size."""
    consumer = _consumer(poll_batch_size=512)

    await consumer.poll(2.5)

    assert consumer._consumer.consume_calls == [(512, 2.5)]


async def test_buffered_poll_calls_do_not_repeat_the_timeout_wait() -> None:
    """Once a batch is buffered, subsequent poll() calls are served
    synchronously from it -- no new consume() call, no new timeout."""
    consumer = _consumer(poll_batch_size=10)
    envelopes = [_envelope(sequence_number=i) for i in range(3)]
    consumer._consumer.enqueue(
        *[_fake_message(e, offset=i) for i, e in enumerate(envelopes)]
    )

    await consumer.poll(5.0)  # triggers the one fetch (timeout=5.0)
    await consumer.poll(0.001)  # served from buffer -- this timeout is unused
    await consumer.poll(999.0)  # also served from buffer -- unused

    assert consumer._consumer.consume_calls == [(10, 5.0)]


async def test_decode_error_mid_batch_does_not_lose_or_reorder_later_records() -> None:
    consumer = _consumer(poll_batch_size=10)
    good_first = _envelope(sequence_number=0)
    good_last = _envelope(sequence_number=2)
    consumer._consumer.enqueue(
        _fake_message(good_first, offset=0),
        _error_message(offset=1, error="simulated broker error"),
        _fake_message(good_last, offset=2),
    )

    first = await consumer.poll(1.0)
    assert first.envelope.sequence_number == 0

    with pytest.raises(EnvelopeDecodeError) as exc_info:
        await consumer.poll(1.0)
    assert exc_info.value.topic == "sceneops.robot.telemetry.v1"
    assert exc_info.value.partition == 0
    assert exc_info.value.offset == 1

    # The batch's third (good) record is still buffered and still
    # reachable, in order, after the error was raised and handled.
    third = await consumer.poll(1.0)
    assert third.envelope.sequence_number == 2
    # Still only one underlying fetch call for all three records.
    assert len(consumer._consumer.consume_calls) == 1


async def test_malformed_record_raises_decode_error_with_location() -> None:
    consumer = _consumer(poll_batch_size=10)
    consumer._consumer.enqueue(
        _FakeMessage(
            topic="sceneops.robot.telemetry.v1",
            partition=0,
            offset=7,
            key=b"robotrun-1",
            headers=[],  # missing every required header
            value=b"payload",
        )
    )

    with pytest.raises(EnvelopeDecodeError) as exc_info:
        await consumer.poll(1.0)
    assert exc_info.value.partition == 0
    assert exc_info.value.offset == 7


async def test_commit_commits_the_last_record_even_when_served_from_buffer() -> None:
    consumer = _consumer(poll_batch_size=10, enable_auto_commit=False)
    envelopes = [_envelope(sequence_number=i) for i in range(2)]
    consumer._consumer.enqueue(
        *[_fake_message(e, offset=i) for i, e in enumerate(envelopes)]
    )

    await consumer.poll(1.0)  # triggers the fetch, returns record 0
    second = await consumer.poll(1.0)  # served from buffer, returns record 1

    await consumer.commit()

    assert len(consumer._consumer.committed) == 1
    committed_message, asynchronous = consumer._consumer.committed[0]
    assert committed_message.offset() == second.offset
    assert asynchronous is False


async def test_close_with_buffered_unreturned_messages_does_not_raise() -> None:
    consumer = _consumer(poll_batch_size=10)
    envelopes = [_envelope(sequence_number=i) for i in range(5)]
    consumer._consumer.enqueue(
        *[_fake_message(e, offset=i) for i, e in enumerate(envelopes)]
    )

    await consumer.poll(1.0)  # fetches all 5, returns 1, buffers 4 more
    assert len(consumer._buffer) == 4

    await consumer.close()

    assert consumer._consumer.closed is True
    # Buffered-but-never-returned records are dropped, not committed --
    # they were never eligible for commit() in the first place (only
    # _last_message, the most recently RETURNED record, ever is), so
    # dropping them loses nothing a future consumer can't redeliver.
    assert consumer._buffer == []
