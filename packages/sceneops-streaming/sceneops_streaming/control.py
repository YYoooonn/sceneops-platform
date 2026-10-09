"""Run lifecycle control events.

An ADDITIVE mechanism for telling a consumer (the run-scoped capture,
``sceneops_recording.capture.consumer``, which finalizes a run only on its
``RUN_END``) when a RobotRun starts and ends, without touching the frozen
``TelemetryEnvelope`` schema, the Kafka wire contract, or any existing
channel's semantics.

**Design: reuse the existing telemetry envelope/topic, a reserved
channel, never a separate control topic.** A control event IS a
``TelemetryEnvelope`` -- same required fields, same
``robot_id``/``robot_run_id``, same Kafka key (``robot_run_id``, via
``wire.partition_key``, entirely unchanged), same topic. What makes it
a control event rather than telemetry is purely its
``channel``/``message_type`` (this module's reserved values, never a
real ROS2 topic/interface, never admitted to the channel registry
(``ChannelSpec`` rejects it), so it can never accidentally get written to
an MCAP even if some caller forgot to
special-case it -- ``McapCaptureWriter.write_envelope`` would reject it
with ``UnsupportedChannelError``, matching every other unknown-channel
case).

Reusing the existing topic (rather than a separate control topic) is
the deliberate choice, for one concrete reason: partitioning by
``robot_run_id`` (frozen, ``streaming-transport.md`` §6) guarantees a
control event lands on the SAME partition as that run's own telemetry,
which is exactly what "ordered consistently with that run's telemetry"
requires -- Kafka only guarantees ordering WITHIN one partition of one
topic, never across two topics/partitions. A separate control topic
would need its own correlation mechanism (e.g. comparing timestamps or
some external sequencing) to establish "this RUN_END happened after
that telemetry record" -- fragile and unnecessary when the existing
transport already provides exact, free, per-partition ordering the
moment the same key is reused. The cost of reusing the telemetry topic
is that every consumer of that topic (not just session-lifecycle-aware
ones) now sees these records too -- mitigated by the reserved channel
making them trivially filterable/ignorable by any consumer that
doesn't care about lifecycle.

**Closing state.** ``RUN_END`` declares ``telemetry_count``: the number of
telemetry sequence numbers the producer assigned to the run, i.e. the size of
the dense ``0..N-1`` sequence space its telemetry occupies (zero for a run
that bridged nothing). It is the one fact a consumer cannot derive from the
telemetry it received, because it is about telemetry that did not arrive: a
consumer that saw ``0..N-1`` and is told the producer assigned ``N`` knows the
stream is complete. ``RUN_START`` carries nothing.

Not a canonical/domain concept -- ``RunEventType`` and
``build_control_envelope``/``is_control_envelope``/``parse_run_event``/
``parse_run_end_telemetry_count`` are transport-level only, matching where
``TelemetryEnvelope`` itself lives.
"""

from __future__ import annotations

import json
import time
from enum import StrEnum

from .constants import SESSION_CONTROL_CHANNEL
from .envelope import EnvelopeEncoding, TelemetryEnvelope


class RunEventType(StrEnum):
    """The two lifecycle signals this phase defines. Additional event
    types (Phase 7.4+, if ever needed) would extend this enum, never
    repurpose an existing value -- ``parse_run_event`` already treats an
    unrecognized ``message_type`` under the control channel as
    "ignore, don't crash," so adding a new value here is backward
    compatible with any consumer running an older version of this
    module."""

    RUN_START = "RUN_START"
    RUN_END = "RUN_END"


_MESSAGE_TYPE_BY_EVENT: dict[RunEventType, str] = {
    RunEventType.RUN_START: "sceneops/control/RunStart",
    RunEventType.RUN_END: "sceneops/control/RunEnd",
}
_EVENT_BY_MESSAGE_TYPE: dict[str, RunEventType] = {
    v: k for k, v in _MESSAGE_TYPE_BY_EVENT.items()
}


class RunEndPayloadError(ValueError):
    """A ``RUN_END`` payload does not declare a valid ``telemetry_count``."""


def build_control_envelope(
    *,
    event_type: RunEventType,
    robot_id: str,
    robot_run_id: str,
    sequence_number: int = 0,
    reason: str | None = None,
    telemetry_count: int | None = None,
) -> TelemetryEnvelope:
    """Build one control-event envelope, ready to publish through the
    same ``TelemetryProducer``/topic as any other telemetry.

    ``sequence_number`` deliberately does NOT need to participate in
    that run's own telemetry sequence counter (``sequence_number`` is
    documented as "diagnostic only, never identity" --
    ``TelemetryEnvelope``'s own field docstring) -- a control event is
    intercepted by capture and validated in its own sequence space,
    independent of telemetry's, so there is no shared numbering invariant
    to preserve here. Callers that want it to reflect "where in the stream this happened" (e.g.
    the ROS2 bridge, which reads its own live counter without
    incrementing it) may pass an explicit value; the default (``0``) is
    fine for any caller that doesn't need that.

    ``source_timestamp_ns`` uses this call's own wall-clock time -- a
    control event has no prior "observation" to preserve (unlike a
    sensor reading); its own emission IS the event, the same reasoning
    ``/mission/status``'s synthetic replay-boundary timestamps already
    document (``streaming-transport.md`` §11.1).

    ``telemetry_count`` is mandatory for ``RUN_END`` and not accepted for
    ``RUN_START`` (see the module docstring); it is the producer's declaration,
    not something this function can check.
    """
    fields: dict[str, object] = {}
    if event_type is RunEventType.RUN_END:
        if telemetry_count is None:
            raise ValueError("RUN_END must declare telemetry_count")
        if isinstance(telemetry_count, bool) or telemetry_count < 0:
            raise ValueError(
                f"telemetry_count must be an integer >= 0, got {telemetry_count!r}"
            )
        fields["telemetry_count"] = telemetry_count
    elif telemetry_count is not None:
        raise ValueError(f"{event_type.value} does not carry telemetry_count")
    if reason:
        fields["reason"] = reason
    payload = json.dumps(fields, sort_keys=True).encode("utf-8")
    return TelemetryEnvelope(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        channel=SESSION_CONTROL_CHANNEL,
        message_type=_MESSAGE_TYPE_BY_EVENT[event_type],
        source_timestamp_ns=time.time_ns(),
        sequence_number=sequence_number,
        encoding=EnvelopeEncoding.JSON,
        payload=payload,
    )


def is_control_envelope(envelope: TelemetryEnvelope) -> bool:
    """True for ANY envelope on the reserved control channel, whether
    or not its ``message_type`` is one this version of the module
    recognizes -- callers use this to decide "route to lifecycle
    handling, never to telemetry/MCAP handling," and
    ``parse_run_event`` separately to decide WHICH event it is (or
    ``None`` if unrecognized)."""
    return envelope.channel == SESSION_CONTROL_CHANNEL


def parse_run_event(envelope: TelemetryEnvelope) -> RunEventType | None:
    """The event type, or ``None`` if ``envelope`` is not a control
    envelope at all, or is one this version doesn't recognize
    (forward-compatible: an unknown future event type is never fatal
    here, only unrecognized -- the caller decides what "unrecognized"
    means for it, e.g. logging and ignoring)."""
    if not is_control_envelope(envelope):
        return None
    return _EVENT_BY_MESSAGE_TYPE.get(envelope.message_type)


def parse_run_end_telemetry_count(envelope: TelemetryEnvelope) -> int:
    """The ``telemetry_count`` a ``RUN_END`` envelope declares. Raises
    ``RunEndPayloadError`` for an envelope that is not a ``RUN_END`` or whose
    payload is not a JSON object with an integer ``telemetry_count >= 0``."""
    if parse_run_event(envelope) is not RunEventType.RUN_END:
        raise RunEndPayloadError("envelope is not a RUN_END control event")
    try:
        payload = json.loads(envelope.payload)
    except ValueError as exc:
        raise RunEndPayloadError(f"RUN_END payload is not JSON: {exc}") from exc
    count = payload.get("telemetry_count") if isinstance(payload, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RunEndPayloadError(
            f"RUN_END must declare an integer telemetry_count >= 0, got {count!r}"
        )
    return count
