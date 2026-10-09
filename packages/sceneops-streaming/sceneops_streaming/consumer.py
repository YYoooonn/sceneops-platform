from __future__ import annotations

import asyncio

from confluent_kafka import OFFSET_BEGINNING, KafkaException, TopicPartition
from confluent_kafka import Consumer as ConfluentConsumer


from .config import StreamingSettings
from .contracts import ConsumedTelemetryEnvelope, KafkaStartPosition
from .errors import EnvelopeDecodeError, StartPositionError
from .wire import decode_envelope

# Default size of the bounded internal fetch buffer (see poll()'s
# docstring). Chosen from a real-Kafka benchmark
# sweeping 8/16/32/64/128/256/512 at a real ~1M-message topic history: throughput rises
# sharply up to ~16-32 (amortizing the asyncio.to_thread dispatch cost
# that motivated this change at all), then plateaus -- 64 reproducibly
# edged out every other candidate tested (~78-79k msg/s vs ~69-75k msg/s
# for 32/128/256/512, confirmed across repeated runs), while still
# keeping the records fetched but not yet returned (re-read after a crash)
# few. See docs/architecture/streaming-multirun-
# phase7-study.md's Phase 7.0.1 follow-up for the full sweep. Not
# exposed as an environment variable -- no operational need for a
# per-deployment override has been demonstrated yet (matches this
# package's existing policy for acks/retries/linger.ms/etc.,
# config.py's own docstring).
DEFAULT_POLL_BATCH_SIZE = 64

# Bound on the metadata and watermark requests that resolve an assignment.
START_VALIDATION_TIMEOUT_SECONDS = 10.0


# librdkafka refuses to create a consumer without a group.id, even one that only
# assign()s. An assigned reader never joins the group and, with auto-commit off,
# never writes to it, so no group by this name ever exists on the broker.
ASSIGNED_READER_GROUP_ID = "sceneops-assigned-reader"


class KafkaTelemetryConsumer:
    """Concrete ``TelemetryConsumer`` (``sceneops_streaming.contracts``)
    backed by ``confluent_kafka``.

    Two ways to read, chosen by the constructor arguments:

    - **Subscribed** (the default): the consumer joins ``group_id`` (default
      ``settings.consumer_group_id``), is rebalanced with the group's other
      members and resumes from the group's committed offsets
      (``auto_offset_reset`` applying when there are none). ``enable_auto_commit``
      controls whether it records progress itself. A caller that needs isolation
      from other consumers (one group per smoke-test invocation) passes its own
      ``group_id``, derived from the configured base, e.g.
      ``f"{settings.consumer_group_id}-smoke-<uuid>"``
      (``tools/e2e/smoke_streaming.py``).
    - **Assigned** (``start_position`` or ``start_from_beginning``): the
      partitions and offsets to read are chosen by the caller and ``assign()``ed
      directly. The consumer never joins a group, is never rebalanced and never
      records progress anywhere: auto-commit is off and there is no commit API,
      so no consumer-group state is created on the broker and none is read.
      ``group_id`` is not accepted. ``auto.offset.reset`` is forced to ``error``
      so an offset that leaves the retained log surfaces as an error instead of
      a silent reset.

    ``start_from_beginning`` assigns every partition of the topic at its
    beginning.

    ``start_position`` assigns one partition at one offset
    (``KafkaStartPosition``) and reads nothing but that partition. The position
    is validated against the live log before the constructor returns
    (``StartPositionError``: unknown topic or partition, offset below the low
    watermark, offset at or after the log end). ``end_offset`` is the partition's
    high watermark when the position was validated (the records the position is
    expected to find already exist below it). Mutually exclusive with
    ``start_from_beginning``.

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
    ``docs/history/streaming-multirun-phase7-study.md`` §4.1) to
    account for ~85-90% of run-scoped capture's apparent rescan cost at
    1M-message topic history -- an implementation detail of this
    wrapper, not the run-scoped-consumer-group architecture itself.

    Records fetched into the buffer but not yet returned are dropped by
    ``close()``; the next attempt reads them again from its own start position.

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
        start_from_beginning: bool = False,
        start_position: KafkaStartPosition | None = None,
    ) -> None:
        if start_position is not None and start_from_beginning:
            raise ValueError("start_position and start_from_beginning are exclusive")
        if start_position is not None and topic not in (None, start_position.topic):
            raise ValueError(
                f"topic={topic!r} contradicts start_position.topic={start_position.topic!r}"
            )
        assigned = start_position is not None or start_from_beginning
        if assigned and group_id is not None:
            raise ValueError("an assigned consumer has no consumer group")
        self._topic = (
            start_position.topic
            if start_position
            else (topic or settings.telemetry_topic)
        )
        self.end_offset: int | None = None
        self._poll_batch_size = poll_batch_size
        self._buffer: list = []
        self._consumer = ConfluentConsumer(
            {
                "bootstrap.servers": settings.bootstrap_servers,
                "group.id": (
                    ASSIGNED_READER_GROUP_ID
                    if assigned
                    else (group_id or settings.consumer_group_id)
                ),
                "auto.offset.reset": "error" if assigned else auto_offset_reset,
                "enable.auto.commit": False if assigned else enable_auto_commit,
            }
        )
        if not assigned:
            self._consumer.subscribe([self._topic])
            return
        try:
            if start_position is not None:
                self.end_offset = self._validate_start(start_position)
                assignment = [
                    TopicPartition(
                        start_position.topic,
                        start_position.partition,
                        start_position.offset,
                    )
                ]
            else:
                assignment = [
                    TopicPartition(self._topic, partition, OFFSET_BEGINNING)
                    for partition in self._partitions_of(self._topic)
                ]
        except BaseException:
            self._consumer.close()
            raise
        self._consumer.assign(assignment)

    def _partitions_of(self, topic: str) -> list[int]:
        timeout = START_VALIDATION_TIMEOUT_SECONDS
        try:
            topic_metadata = self._consumer.list_topics(
                topic, timeout=timeout
            ).topics.get(topic)
        except KafkaException as exc:
            raise StartPositionError(
                f"cannot read metadata of topic {topic!r}: {exc}"
            ) from exc
        if topic_metadata is None or topic_metadata.error is not None:
            raise StartPositionError(f"topic {topic!r} does not exist")
        return sorted(topic_metadata.partitions)

    def _validate_start(self, position: KafkaStartPosition) -> int:
        """Returns the partition's high watermark."""
        timeout = START_VALIDATION_TIMEOUT_SECONDS
        partitions = self._partitions_of(position.topic)
        if position.partition not in partitions:
            raise StartPositionError(
                f"topic {position.topic!r} has no partition {position.partition} "
                f"(partitions: {partitions})"
            )

        try:
            low, high = self._consumer.get_watermark_offsets(
                TopicPartition(position.topic, position.partition),
                timeout=timeout,
                cached=False,
            )
        except KafkaException as exc:
            raise StartPositionError(
                f"cannot read watermarks of {position.topic}[{position.partition}]: "
                f"{exc}"
            ) from exc
        if position.offset < low:
            raise StartPositionError(
                f"offset {position.offset} of {position.topic}[{position.partition}] "
                f"is below the low watermark {low}: no longer retained"
            )
        if position.offset >= high:
            raise StartPositionError(
                f"offset {position.offset} of {position.topic}[{position.partition}] "
                f"is not in the log (high watermark {high})"
            )
        return high

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

        return ConsumedTelemetryEnvelope(
            envelope=envelope,
            key=msg.key() or b"",
            topic=topic,
            partition=partition,
            offset=offset,
        )

    async def close(self) -> None:
        # Fetched-but-not-yet-returned records are dropped; the next attempt
        # reads them again.
        self._buffer = []
        await asyncio.to_thread(self._consumer.close)
