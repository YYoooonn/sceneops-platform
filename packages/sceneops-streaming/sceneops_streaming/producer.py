from __future__ import annotations

import asyncio
import time

from confluent_kafka import KafkaException
from confluent_kafka import Producer as ConfluentProducer


from .config import StreamingSettings
from .contracts import DeliveredRecord
from .envelope import TelemetryEnvelope
from .errors import DeliveryError, ProducerQueueFullError
from .wire import EncodedTelemetryRecord, encode_envelope


class KafkaTelemetryProducer:
    """Concrete ``TelemetryProducer`` (``sceneops_streaming.contracts``)
    backed by ``confluent_kafka``. The only place in this package that owns
    a live broker connection for producing.

    ``publish_and_wait`` is the synchronous exception: it returns the position
    the broker stored one record at, for the few records (a run's ``RUN_START``)
    whose position is handed to another process.

    ``publish`` buffers the record client-side and returns once librdkafka
    has accepted it into its internal queue, not once the broker has
    acknowledged it -- call ``flush`` to block until every buffered record
    is actually delivered (or raise on a delivery failure).

    A record librdkafka refuses because its queue is full is not accepted:
    ``publish`` raises ``ProducerQueueFullError`` and the record exists nowhere
    else. ``flush`` only speaks for accepted records, so it succeeds after such
    a rejection; the caller that saw the error owns the knowledge that a record
    is missing.

    The producer is idempotent. Within one producer instance, broker
    retries (connection loss, lost responses, request timeouts) neither
    duplicate nor reorder records of a partition. A record whose delivery
    finally fails (``message.timeout.ms``) is reported through ``flush``;
    later records may still be delivered, leaving a gap, so a delivery
    error invalidates the run's stream. Idempotence does not span producer
    instances: a restarted producer can re-send records.
    """

    def __init__(
        self,
        *,
        settings: StreamingSettings,
        topic: str | None = None,
        queue_full_timeout_seconds: float = 1.0,
    ) -> None:
        self._topic = topic or settings.telemetry_topic
        self._queue_full_timeout_seconds = queue_full_timeout_seconds
        # Set when a record exhausted its queue-full wait, cleared by the next
        # record that is accepted. While set, a full queue refuses at once: the
        # stream already has a gap, and a source that keeps producing against an
        # unreachable broker must not be slowed to one record per wait.
        self._queue_stayed_full = False
        self._producer = ConfluentProducer(
            {
                "bootstrap.servers": settings.bootstrap_servers,
                "client.id": settings.producer_client_id,
                # Implies max.in.flight<=5 and unlimited retries; with it a
                # retried batch can neither duplicate nor overtake another.
                "enable.idempotence": True,
                "acks": "all",
            }
        )
        self._delivery_error: KafkaException | None = None

    async def publish(self, envelope: TelemetryEnvelope) -> None:
        record = encode_envelope(envelope)
        await asyncio.to_thread(self._produce_sync, record)

    def _produce_sync(self, record: EncodedTelemetryRecord) -> None:
        # A queue slot is released only when the record's delivery report is
        # served, and only poll()/flush() serve reports -- produce() never does.
        # Serving them first keeps the queue holding only records still in
        # flight; without it a full queue never drains, even once the broker is
        # healthy again.
        self._producer.poll(0)
        wait = 0.0 if self._queue_stayed_full else self._queue_full_timeout_seconds
        deadline = time.monotonic() + wait
        while True:
            try:
                self._producer.produce(
                    topic=self._topic,
                    key=record.key,
                    value=record.value,
                    headers=record.headers,
                    on_delivery=self._on_delivery,
                )
                self._queue_stayed_full = False
                return
            except BufferError:
                # Rejected atomically: nothing was queued, so retrying cannot
                # duplicate or reorder. Waiting in poll() (not spinning on
                # produce()) is bounded, and returns as soon as a report frees
                # a slot.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._queue_stayed_full = True
                    raise ProducerQueueFullError(
                        f"Kafka producer queue is full (waited {wait}s); the "
                        "record was not accepted"
                    ) from None
                self._producer.poll(remaining)

    async def publish_and_wait(
        self, envelope: TelemetryEnvelope, timeout_seconds: float = 10.0
    ) -> DeliveredRecord:
        record = encode_envelope(envelope)
        return await asyncio.to_thread(
            self._produce_and_wait_sync, record, timeout_seconds
        )

    def _produce_and_wait_sync(
        self, record: EncodedTelemetryRecord, timeout_seconds: float
    ) -> DeliveredRecord:
        outcome: list[DeliveredRecord | DeliveryError] = []

        def on_delivery(err: object, msg: object) -> None:
            if err is not None:
                outcome.append(DeliveryError(str(err)))
            else:
                outcome.append(
                    DeliveredRecord(msg.topic(), msg.partition(), msg.offset())
                )

        self._producer.produce(
            topic=self._topic,
            key=record.key,
            value=record.value,
            headers=record.headers,
            on_delivery=on_delivery,
        )
        # poll() runs the delivery callbacks on this thread. The error of this
        # record is returned to the caller here, never left for a later flush.
        deadline = time.monotonic() + timeout_seconds
        while not outcome:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"no delivery report within {timeout_seconds}s; the record "
                    "may still be stored later"
                )
            self._producer.poll(min(remaining, 0.1))
        result = outcome[0]
        if isinstance(result, DeliveryError):
            raise result
        return result

    def _on_delivery(self, err: object, _msg: object) -> None:
        if err is not None:
            self._delivery_error = KafkaException(err)

    async def flush(self, timeout_seconds: float = 10.0) -> None:
        remaining = await asyncio.to_thread(self._producer.flush, timeout_seconds)
        if remaining > 0:
            raise TimeoutError(
                f"{remaining} telemetry record(s) still undelivered after "
                f"{timeout_seconds}s flush timeout"
            )
        if self._delivery_error is not None:
            error, self._delivery_error = self._delivery_error, None
            raise error

    async def close(self, timeout_seconds: float = 10.0) -> None:
        await self.flush(timeout_seconds)
