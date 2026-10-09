"""The lifecycle of one streamed RobotRun as seen by Capture.

A lifecycle-governed capture (``run_capture(stop_on_run_end=True)``) enforces

    ARMED --RUN_START--> CAPTURING --RUN_END--> ENDED

    ARMED      waiting for the run's RUN_START; records of other runs are
               ignored, and nothing of the target run but RUN_START is valid
    CAPTURING  RUN_START seen; telemetry is accepted
    ENDED      RUN_END seen; Capture consumes nothing further

RUN_END declares how many telemetry sequence numbers the producer assigned
(``telemetry_count``). Capture accepts the run only if it observed exactly that
many -- the telemetry tracker has already proven they are the dense 0..N-1
sequence, so a matching count proves nothing at the head, in the middle or at the
tail was lost. See ``check_run_complete``.

Everything after ENDED (validate, receipt, atomic finalize, Kafka commit) is
linear code in ``consumer.run_capture``, not event-driven, so it is not a
phase here. The invariant that matters across it is that no offset is
committed before the bag is durably finalized.

The machine rejects, loudly and at the first offending record, what the
sequence trackers would otherwise only catch indirectly and late (a missing
RUN_START surfaced only when RUN_END arrived with control sequence 1): a stream
that is not a complete RUN_START ... RUN_END session of exactly this run never
becomes a finalized recording. One ``robot_run_id`` is exactly one streaming
session; a second RUN_START is a violation, never a restart.
"""

from __future__ import annotations

from enum import StrEnum

from sceneops_streaming import RunEventType


class RunPhase(StrEnum):
    ARMED = "armed"
    CAPTURING = "capturing"
    ENDED = "ended"


class RunLifecycleError(RuntimeError):
    """The target run's records violate its RUN_START -> telemetry -> RUN_END
    lifecycle (telemetry or RUN_END before RUN_START, a second RUN_START, a
    record after RUN_END). Nothing is finalized or committed."""


class RunIncompleteError(RunLifecycleError):
    """RUN_END declares a telemetry count different from what Capture observed
    (telemetry lost after the last record Capture saw, or a RUN_END that does not
    describe this stream). Nothing is finalized or committed."""


def check_run_complete(*, declared: int, observed: int) -> None:
    """``declared`` is RUN_END's ``telemetry_count``; ``observed`` is the number
    of distinct telemetry sequence numbers Capture accepted (0..observed-1)."""
    if declared == observed:
        return
    detail = (
        f"{declared - observed} missing after the last observed record"
        if declared > observed
        else f"{observed - declared} more than the producer assigned"
    )
    raise RunIncompleteError(
        f"RUN_END declares {declared} telemetry record(s) but Capture observed "
        f"{observed}: {detail}"
    )


class RunLifecycle:
    def __init__(self) -> None:
        self.phase = RunPhase.ARMED
        # True while RUN_START is the latest record accepted for the run: the
        # only position at which an exact redelivery of it is a benign
        # duplicate rather than a second session.
        self._run_start_is_latest = False

    def on_telemetry(self) -> None:
        if self.phase is not RunPhase.CAPTURING:
            raise RunLifecycleError(
                f"telemetry in phase {self.phase.value}: "
                + (
                    "it precedes RUN_START (a start offset after RUN_START, or "
                    "a bridge that never published it)"
                    if self.phase is RunPhase.ARMED
                    else "it follows RUN_END"
                )
            )
        self._run_start_is_latest = False

    def on_control(self, event: RunEventType | None, *, redelivery: bool) -> None:
        """``redelivery`` is the control sequence tracker's verdict that this
        envelope repeats the previous control envelope exactly (same sequence
        number, same payload). The payload does not encode the event type, so
        a RUN_END that repeats a RUN_START exactly arrives here as a
        "redelivery" of a different event and is rejected below.

        An unrecognized event type (a future control event) is sequence
        validated by the caller and otherwise ignored."""
        if event is None:
            return

        if redelivery:
            if not (
                event is RunEventType.RUN_START
                and self.phase is RunPhase.CAPTURING
                and self._run_start_is_latest
            ):
                raise RunLifecycleError(
                    f"{event.value} repeats the previous control envelope but is "
                    f"not an immediate redelivery of RUN_START (phase "
                    f"{self.phase.value})"
                )
            return

        if event is RunEventType.RUN_START:
            if self.phase is not RunPhase.ARMED:
                raise RunLifecycleError(
                    f"second RUN_START in phase {self.phase.value}: a "
                    "robot_run_id is exactly one streaming session"
                )
            self.phase = RunPhase.CAPTURING
            self._run_start_is_latest = True
        elif event is RunEventType.RUN_END:
            if self.phase is not RunPhase.CAPTURING:
                raise RunLifecycleError(
                    f"RUN_END in phase {self.phase.value}"
                    + (
                        ": it precedes RUN_START"
                        if self.phase is RunPhase.ARMED
                        else ""
                    )
                )
            self.phase = RunPhase.ENDED
            self._run_start_is_latest = False
