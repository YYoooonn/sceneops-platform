from __future__ import annotations


class EnvelopeDecodeError(ValueError):
    """A Kafka record could not be reconstructed into a valid
    ``TelemetryEnvelope`` -- missing/unparseable header, invalid UTF-8, or a
    value that fails ``TelemetryEnvelope`` validation (unknown version,
    non-positive timestamp, etc).

    Malformed records are never silently coerced or dropped -- this is the
    one typed failure raised for all of those cases, always with
    topic/partition/offset context attached by the caller
    (``KafkaTelemetryConsumer.poll``) so a bad record is diagnosable
    without a debugger. No DLQ/retry policy exists -- that is a
    reliability-boundary concern; the caller decides whether to stop,
    skip, or surface the failure.
    """

    def __init__(
        self,
        message: str,
        *,
        topic: str | None = None,
        partition: int | None = None,
        offset: int | None = None,
    ) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset
        location = ""
        if topic is not None:
            location = f" [{topic}:{partition}@{offset}]"
        super().__init__(f"{message}{location}")


class StartPositionError(RuntimeError):
    """A ``KafkaStartPosition`` cannot be used: its topic or partition does not
    exist, or its offset is outside the retained log (below the low watermark,
    or at/after the end). The consumer never substitutes another position."""


class ReplayExpiredError(StartPositionError):
    """Records an attempt needs have left the retained log: its start offset is
    below the low watermark, or the log start moved past its read position while
    it was consuming. Kafka retention removed them; no position of this attempt
    can read them again, and the consumer never resets to another one.

    Distinct from a position that is not in the log yet (at or after the high
    watermark), which is the run not having started."""


class DeliveryError(RuntimeError):
    """The broker did not acknowledge a published record (for example its
    ``message.timeout.ms`` expired). The record is not stored, unless a late
    attempt still succeeds -- see ``TelemetryProducer.publish_and_wait``."""


class ProducerQueueFullError(RuntimeError):
    """A record was not accepted: librdkafka's client-side queue was full and
    stayed full for the producer's ``queue_full_timeout_seconds`` (or an earlier
    record had already waited that long and no record has been accepted since,
    so this one is refused at once). Nothing was queued, so the record exists
    only in the caller, and a later record that is accepted leaves a gap."""
