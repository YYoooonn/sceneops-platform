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
    configured application-level default) -- a caller that doesn't need
    per-instance isolation can construct this with no
    ``group_id`` at all. A caller that DOES need isolation (one group per
    RobotRun replay, one per smoke-test invocation) passes its own,
    derived from that same configured base rather than an unrelated
    literal -- e.g. ``f"{settings.consumer_group_id}-smoke-<uuid>"``
    (``scripts/e2e/smoke_streaming.py``). This class never invents a
    suffix itself; group identity/rebalancing policy beyond the
    configured default is a caller concern, not a transport one.

    ``enable_auto_commit`` defaults to ``True`` (unchanged default
    behavior for every existing caller). A durability-sensitive caller
    (e.g. the MCAP capture consumer, which must never acknowledge a
    record as durably captured before the recording holding it is
    finalized on disk) passes ``enable_auto_commit=False`` and calls
    ``commit()`` explicitly once it has established its own durability
    boundary -- never relying on librdkafka's periodic background commit.
    """

    def __init__(
        self,
        *,
        settings: StreamingSettings,
        group_id: str | None = None,
        topic: str | None = None,
        auto_offset_reset: str = "earliest",
        enable_auto_commit: bool = True,
    ) -> None:
        self._topic = topic or settings.telemetry_topic
        self._last_message: object | None = None
        self._consumer = ConfluentConsumer(
            {
                "bootstrap.servers": settings.bootstrap_servers,
                "group.id": group_id or settings.consumer_group_id,
                "auto.offset.reset": auto_offset_reset,
                "enable.auto.commit": enable_auto_commit,
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

        self._last_message = msg
        return ConsumedTelemetryEnvelope(
            envelope=envelope,
            key=msg.key() or b"",
            topic=topic,
            partition=partition,
            offset=offset,
        )

    async def commit(self) -> None:
        """Synchronously commit up through the most recently returned
        record. Only meaningful with ``enable_auto_commit=False`` --
        the caller is asserting its own durability boundary has been
        reached (e.g. an MCAP recording has been validated and
        atomically finalized), not merely that the record was consumed
        into process memory."""
        if self._last_message is None:
            return
        await asyncio.to_thread(
            self._consumer.commit, message=self._last_message, asynchronous=False
        )

    async def close(self) -> None:
        await asyncio.to_thread(self._consumer.close)
