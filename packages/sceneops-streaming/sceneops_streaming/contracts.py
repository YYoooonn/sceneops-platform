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
