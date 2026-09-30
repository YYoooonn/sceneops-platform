from __future__ import annotations

import asyncio

from confluent_kafka import Consumer as ConfluentConsumer
from confluent_kafka import TopicPartition

from sceneops_core.streaming import ConsumedTelemetryEnvelope

from .config import StreamingSettings
from .errors import EnvelopeDecodeError
from .wire import decode_envelope

# Default size of the bounded internal fetch buffer (see poll()'s
# docstring). Chosen from a real-Kafka benchmark
# (scripts/dev/phase7/poll_batch_size_benchmark.py) sweeping 8/16/32/64/
# 128/256/512 at a real ~1M-message topic history: throughput rises
# sharply up to ~16-32 (amortizing the asyncio.to_thread dispatch cost
# that motivated this change at all), then plateaus -- 64 reproducibly
# edged out every other candidate tested (~78-79k msg/s vs ~69-75k msg/s
# for 32/128/256/512, confirmed across repeated runs), while still
# keeping worst-case buffered-but-uncommitted state (the redelivery-on-
# crash replay window) small. See docs/architecture/streaming-multirun-
# phase7-study.md's Phase 7.0.1 follow-up for the full sweep. Not
# exposed as an environment variable -- no operational need for a
# per-deployment override has been demonstrated yet (matches this
# package's existing policy for acks/retries/linger.ms/etc.,
# config.py's own docstring).
DEFAULT_POLL_BATCH_SIZE = 64


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

    **Internal fetch batching (Phase 7.0.1).** ``poll()``'s PUBLIC
    contract is unchanged: one call returns one decoded record (or
    ``None`` on timeout), same as before. Internally, it no longer
    calls ``confluent_kafka.Consumer.poll()`` (one broker round trip,
    one ``asyncio.to_thread`` transition, PER MESSAGE) -- it calls
    ``Consumer.consume(num_messages=poll_batch_size, timeout=...)``
    (one bounded blocking C call, one ``asyncio.to_thread`` transition,
    per UP-TO-``poll_batch_size`` messages) and serves the results out
    of a small in-process buffer, one per ``poll()`` call, in the exact
    order Kafka returned them. This was measured (Phase 7.0's study,
    ``docs/architecture/streaming-multirun-phase7-study.md`` §4.1) to
    account for ~85-90% of run-scoped capture's apparent rescan cost at
    1M-message topic history -- an implementation detail of this
    wrapper, not the run-scoped-consumer-group architecture itself.

    Nothing about Kafka's own delivery/commit contract changes: records
    pulled into the buffer are exactly as "fetched but not yet
    committed" as a single-record ``poll()`` result always was --
    ``commit()`` only ever acknowledges the most recently RETURNED (to
    the caller) message (``_last_message``, updated on every ``poll()``
    return, whether served fresh or from the buffer), never anything
    still sitting in the buffer. A crash with buffered-but-unreturned
    messages loses nothing: they were never committed, so a fresh
    consumer in the same group redelivers them from the last committed
    offset, identically to today's single-record behavior.

    **Timeout semantics.** ``timeout_seconds`` bounds the underlying
    blocking fetch, exactly as before -- it is never multiplied by
    ``poll_batch_size``: ``consume(num_messages=N, timeout=T)`` returns
    as soon as it has >=1 message or ``T`` elapses, whichever is
    first, the same single bounded wait ``poll(timeout=T)`` always
    made. The subtlety this introduces: a call that finds the buffer
    already non-empty (because a PRIOR call's fetch returned more than
    one message) returns immediately, synchronously, WITHOUT waiting or
    even inspecting ``timeout_seconds`` at all -- only the call that
    triggers an actual fetch (buffer empty) is timeout-bounded. A
    caller that expects every ``poll()`` call to take up to
    ``timeout_seconds`` (e.g. for its own coarse pacing) will observe
    bursts of near-zero-latency calls interleaved with occasional
    full-fetch calls instead -- total throughput is higher, but
    per-call latency is no longer uniform. No current caller relies on
    per-call latency being uniform (every caller either loops until a
    stop condition or a fixed message count, never on wall-clock pacing
    of individual ``poll()`` calls).
    """

    def __init__(
        self,
        *,
        settings: StreamingSettings,
        group_id: str | None = None,
        topic: str | None = None,
        auto_offset_reset: str = "earliest",
        enable_auto_commit: bool = True,
        poll_batch_size: int = DEFAULT_POLL_BATCH_SIZE,
    ) -> None:
        self._topic = topic or settings.telemetry_topic
        self._last_message: object | None = None
        self._poll_batch_size = poll_batch_size
        self._buffer: list = []
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
        if not self._buffer:
            self._buffer = await asyncio.to_thread(
                self._consumer.consume,
                num_messages=self._poll_batch_size,
                timeout=timeout_seconds,
            )
            if not self._buffer:
                return None

        msg = self._buffer.pop(0)
        return self._process_message(msg)

    def _process_message(self, msg: object) -> ConsumedTelemetryEnvelope:
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

    async def commit_offsets(self, offsets: dict[int, int]) -> None:
        """Explicitly commit a next-offset-to-read position per Kafka
        partition (Phase 7.1) -- for a caller that must commit to a
        position OTHER than "the most recently returned record"
        (``commit()``'s only mode). The motivating case: a single
        continuous consumer multiplexing many independent downstream
        consumers (e.g. one open MCAP writer per RobotRun) can only
        safely advance a partition's committed offset up to the
        earliest position still needed by any of them -- never simply
        "the last record this process happened to see," which could
        acknowledge (and thus make Kafka stop redelivering) a record
        some other, still-open downstream consumer has not yet
        durably persisted.

        ``offsets`` maps ``partition -> offset``, where ``offset`` is
        the position a FRESH consumer in this group should read NEXT
        (``confluent_kafka``'s own commit convention -- one past the
        last position that's safe to consider durably handled, not
        the last-consumed offset itself). Caller-computed; this method
        performs no safety reasoning of its own, only the mechanical
        commit. A partition absent from ``offsets`` is left untouched.
        """
        if not offsets:
            return
        topic_partitions = [
            TopicPartition(self._topic, partition, offset)
            for partition, offset in offsets.items()
        ]
        await asyncio.to_thread(
            self._consumer.commit, offsets=topic_partitions, asynchronous=False
        )

    async def close(self) -> None:
        # Any still-buffered (fetched-but-not-yet-returned) messages are
        # simply dropped -- they were never committed (commit() only
        # ever acknowledges self._last_message, the most recently
        # RETURNED record), so they remain safely replayable from the
        # last committed offset by a future consumer in this same
        # group, identical to how an in-flight single-record poll()
        # result was always handled before this change.
        self._buffer = []
        await asyncio.to_thread(self._consumer.close)
