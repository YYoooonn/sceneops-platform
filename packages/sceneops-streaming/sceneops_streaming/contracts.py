from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .envelope import TelemetryEnvelope


@dataclass(frozen=True)
class ConsumedTelemetryEnvelope:
    """One reconstructed ``TelemetryEnvelope`` plus the Kafka position it
    was read from.

    Topic/partition/offset are transport metadata -- diagnostic and
    reproduction-useful, never part of canonical robot telemetry
    semantics. Kept as a separate wrapper rather than fields on
    ``TelemetryEnvelope`` itself so the logical envelope stays exactly
    what a producer publishes, with nothing a consumer-only concept could
    leak backward into it.
    """

    envelope: TelemetryEnvelope
    key: bytes
    topic: str
    partition: int
    offset: int


@dataclass(frozen=True)
class DeliveredRecord:
    """Where the broker stored one record, as reported by its delivery
    acknowledgement.

    Produced by ``TelemetryProducer.publish_and_wait`` only after the
    broker acknowledged the record, so the position is a fact, not a guess.
    A transport fact of one publish: not part of the envelope and not
    RobotRun identity.
    """

    topic: str
    partition: int
    offset: int

    def as_start_position(self) -> KafkaStartPosition:
        return KafkaStartPosition(self.topic, self.partition, self.offset)


@dataclass(frozen=True)
class KafkaStartPosition:
    """Where a pinned consumer starts reading: one partition of one topic at
    one offset.

    Execution information for a single consume attempt -- never part of a
    RobotRun's canonical identity or metadata. The offset is a validated hint,
    not a promise: ``KafkaTelemetryConsumer`` checks it against the live log and
    fails with ``StartPositionError`` instead of resetting to another position.
    """

    topic: str
    partition: int
    offset: int

    def __post_init__(self) -> None:
        if not self.topic:
            raise ValueError("topic must not be empty")
        if self.partition < 0:
            raise ValueError(f"partition must be >= 0, got {self.partition}")
        if self.offset < 0:
            raise ValueError(f"offset must be >= 0, got {self.offset}")

    def __str__(self) -> str:
        return f"{self.topic}:{self.partition}:{self.offset}"

    @classmethod
    def parse(cls, text: str) -> KafkaStartPosition:
        """Inverse of ``str()``: ``TOPIC:PARTITION:OFFSET``. The text form is how
        a launcher hands the position to a Capture process. Kafka topic names
        cannot contain ``:``."""
        try:
            topic, partition, offset = text.rsplit(":", 2)
            return cls(topic, int(partition), int(offset))
        except ValueError as exc:
            raise ValueError(
                f"expected TOPIC:PARTITION:OFFSET, got {text!r}: {exc}"
            ) from exc


@runtime_checkable
class TelemetryProducer(Protocol):
    """Port-like contract for publishing telemetry, mirroring
    ``sceneops_core.artifacts.contracts.ArtifactStore``'s port/adapter
    split -- this package defines the contract only, never a Kafka client
    import. See ``sceneops_streaming.producer.KafkaTelemetryProducer`` for
    the concrete implementation.
    """

    async def publish(self, envelope: TelemetryEnvelope) -> None:
        """Publish one envelope. At-least-once delivery -- a caller that
        needs a delivery guarantee beyond "sent to the broker" must handle
        its own retry/idempotency; this contract makes no exactly-once
        claim end-to-end."""

    async def publish_and_wait(
        self, envelope: TelemetryEnvelope, timeout_seconds: float = 10.0
    ) -> DeliveredRecord:
        """Publish one envelope and return only once the broker acknowledged it,
        with the position it stored it at. Raises ``DeliveryError`` if the
        delivery failed and ``TimeoutError`` if no outcome arrived in time (the
        record may still be stored later). For the rare record whose position
        matters; bulk telemetry uses ``publish``."""

    async def flush(self, timeout_seconds: float = 10.0) -> None:
        """Block until all previously published envelopes are acknowledged
        by the broker (or ``timeout_seconds`` elapses)."""

    async def close(self) -> None:
        """Release the underlying client. Idempotent."""


@runtime_checkable
class TelemetryConsumer(Protocol):
    """Port-like contract for consuming telemetry. See
    ``sceneops_streaming.consumer.KafkaTelemetryConsumer``.
    """

    async def poll(
        self, timeout_seconds: float = 1.0
    ) -> ConsumedTelemetryEnvelope | None:
        """Return the next available record, or ``None`` on timeout.

        Raises a decode error (never silently drops or coerces) if a
        record is read but fails to reconstruct into a valid
        ``TelemetryEnvelope`` -- see
        ``sceneops_streaming.errors.EnvelopeDecodeError``.
        """

    async def close(self) -> None:
        """Release the underlying client. Idempotent."""
