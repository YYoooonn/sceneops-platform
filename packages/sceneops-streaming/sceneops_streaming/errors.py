from __future__ import annotations


class EnvelopeDecodeError(ValueError):
    """A Kafka record could not be reconstructed into a valid
    ``TelemetryEnvelope`` -- missing/unparseable header, invalid UTF-8, or a
    value that fails ``TelemetryEnvelope`` validation (unknown version,
    non-positive timestamp, etc).

    Phase 6.1 request §14: malformed records are never silently coerced or
    dropped -- this is the one typed failure raised for all of those cases,
    always with topic/partition/offset context attached by the caller
    (``KafkaTelemetryConsumer.poll``) so a bad record is diagnosable without
    a debugger. No DLQ/retry policy exists yet (that's a reliability-phase
    concern, explicitly out of scope here) -- the caller decides whether to
    stop, skip, or surface the failure.
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
