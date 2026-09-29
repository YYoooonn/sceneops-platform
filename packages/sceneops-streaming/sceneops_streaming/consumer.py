from __future__ import annotations

import asyncio

from confluent_kafka import Consumer as ConfluentConsumer

from sceneops_core.streaming import ConsumedTelemetryEnvelope

from .config import StreamingSettings
from .errors import EnvelopeDecodeError
from .wire import decode_envelope


class KafkaTelemetryConsumer:
    """Concrete ``TelemetryConsumer`` (``sceneops_core.streaming.contracts``)
    backed by ``confluent_kafka``.

    ``group_id`` defaults to ``settings.consumer_group_id`` (the one
    configured application-level default, request §8) -- a caller that
    doesn't need per-instance isolation can construct this with no
    ``group_id`` at all. A caller that DOES need isolation (one group per
    RobotRun replay, one per smoke-test invocation) passes its own,
    derived from that same configured base rather than an unrelated
    literal -- e.g. ``f"{settings.consumer_group_id}-smoke-<uuid>"``
    (``scripts/e2e/smoke_streaming.py``). This class never invents a
    suffix itself; group identity/rebalancing policy beyond the
    configured default is a caller concern, not a transport one.
    """

    def __init__(
        self,
        *,
        settings: StreamingSettings,
        group_id: str | None = None,
        topic: str | None = None,
        auto_offset_reset: str = "earliest",
    ) -> None:
        self._topic = topic or settings.telemetry_topic
        self._consumer = ConfluentConsumer(
            {
                "bootstrap.servers": settings.bootstrap_servers,
                "group.id": group_id or settings.consumer_group_id,
                "auto.offset.reset": auto_offset_reset,
                "enable.auto.commit": True,
            }
        )
        self._consumer.subscribe([self._topic])

    async def poll(
        self, timeout_seconds: float = 1.0
    ) -> ConsumedTelemetryEnvelope | None:
        msg = await asyncio.to_thread(self._consumer.poll, timeout_seconds)
        if msg is None:
            return None

        topic, partition, offset = msg.topic(), msg.partition(), msg.offset()

        if msg.error() is not None:
            raise EnvelopeDecodeError(
                f"Kafka consumer error: {msg.error()}",
                topic=topic,
                partition=partition,
                offset=offset,
            )

        try:
            envelope = decode_envelope(headers=msg.headers(), value=msg.value())
        except EnvelopeDecodeError as exc:
            raise EnvelopeDecodeError(
                str(exc), topic=topic, partition=partition, offset=offset
            ) from exc

        return ConsumedTelemetryEnvelope(
            envelope=envelope,
            key=msg.key() or b"",
            topic=topic,
            partition=partition,
            offset=offset,
        )

    async def close(self) -> None:
        await asyncio.to_thread(self._consumer.close)
