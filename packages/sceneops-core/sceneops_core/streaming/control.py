"""Run lifecycle control events.

An ADDITIVE mechanism for telling a consumer (the run-scoped capture,
``ros2/capture/capture_consumer.py``, which finalizes a run only on its
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

Not a canonical/domain concept -- ``RunEventType`` and
``build_control_envelope``/``is_control_envelope``/``parse_control_event``
are transport-level only, matching where ``TelemetryEnvelope`` itself
lives.
"""

from __future__ import annotations

import json
import time
from enum import StrEnum

from sceneops_core.constants.streaming import SESSION_CONTROL_CHANNEL

from .schemas import EnvelopeEncoding, TelemetryEnvelope


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


def build_control_envelope(
    *,
    event_type: RunEventType,
    robot_id: str,
    robot_run_id: str,
    sequence_number: int = 0,
    reason: str | None = None,
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
    """
    payload = json.dumps({"reason": reason}).encode("utf-8") if reason else b"{}"
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
