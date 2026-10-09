from __future__ import annotations

import asyncio

from confluent_kafka import KafkaException
from confluent_kafka import Producer as ConfluentProducer


from .config import StreamingSettings
from .envelope import TelemetryEnvelope
from .wire import EncodedTelemetryRecord, encode_envelope


class KafkaTelemetryProducer:
    """Concrete ``TelemetryProducer`` (``sceneops_streaming.contracts``)
    backed by ``confluent_kafka``. The only place in this package that owns
    a live broker connection for producing.

    ``publish`` buffers the record client-side and returns once librdkafka
    has accepted it into its internal queue, not once the broker has
    acknowledged it -- call ``flush`` to block until every buffered record
    is actually delivered (or raise on a delivery failure).

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
    ) -> None:
        self._topic = topic or settings.telemetry_topic
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
        self._producer.produce(
            topic=self._topic,
            key=record.key,
            value=record.value,
            headers=record.headers,
            on_delivery=self._on_delivery,
        )
        # Services the delivery-report queue without blocking -- keeps
        # librdkafka's internal queue from filling up on a long publish burst.
        self._producer.poll(0)

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

    async def close(self) -> None:
        await self.flush()
