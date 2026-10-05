"""Continuous multi-RobotRun capture router with explicit session
lifecycle (Phase 7.1 core runtime + Phase 7.2 lifecycle).

One long-lived Kafka consumer, routing each consumed record by
``TelemetryEnvelope.robot_run_id`` to that run's own, independently
sequence-tracked, independently written MCAP -- replacing N independent
full-topic-history rescans (one per ``run_capture()`` invocation, the
existing ``RunScopedCapture`` path in ``capture_consumer.py``) with one
continuous topic pass serving arbitrarily many concurrent RobotRuns.

Phase 7.2 adds an explicit ``CaptureSession`` lifecycle
(``SessionState``: ``DISCOVERED -> RECORDING -> FINALIZING ->
FINALIZED``, or ``-> FAILED``), driven by two signals: an explicit,
additive Kafka control event (``sceneops_core.streaming.control`` --
``RUN_START``/``RUN_END``, reused unmodified) and a configurable
per-session idle-timeout fallback for when a producer disappears
without ever sending ``RUN_END``.

This module composes ``capture_consumer.py``'s own building blocks
(``_RunFilter``, ``_SequenceTracker``, ``_sha256_file``) and
``finalize.py``/``validation.py``/``mcap_writer.py`` directly -- it does
not reimplement any of them, and it does not modify them. The frozen
commit-boundary ordering they encode (write -> close/fsync -> validate
-> atomically finalize -> only then advance Kafka position) is
preserved per-session; see ``ContinuousCaptureRouter``'s own docstring
for how that ordering composes across MANY simultaneously-open
sessions, and how the lifecycle model interacts with the conservative
offset-commit-safety policy Phase 7.1 introduced.

``RunScopedCapture`` (``capture_consumer.run_capture``) is untouched and
remains the supported path for replay/backfill/debugging/recovery --
this module is an additional, independent consumer of the same frozen
Kafka/envelope/MCAP contracts, not a replacement.

``CaptureSession`` is execution/runtime state only -- in-process,
never persisted, never a canonical domain record. It is not
``RobotRun``, and nothing here writes to PostgreSQL/ArtifactStore
(Phase 7.2's own explicit scope exclusions).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sceneops_core.streaming import (
    DEFAULT_REGISTRY,
    ChannelRegistry,
    ConsumedTelemetryEnvelope,
    RunEventType,
    is_control_envelope,
    parse_run_event,
)
from sceneops_streaming.config import StreamingSettings
from sceneops_streaming.consumer import KafkaTelemetryConsumer
from sceneops_streaming.errors import EnvelopeDecodeError

import capture_consumer
from capture_consumer import CaptureResult, PartitionInvariantError, SequenceIntegrityError
from finalize import finalize_bag, partial_bag_path, prepare_partial_bag_dir
from group_id import derive_capture_group_id
from mcap_writer import McapCaptureWriter, UnsupportedChannelError
from validation import validate_mcap_file

# The continuous router's own consumer-group base -- deliberately
# DISTINCT from capture_consumer.CAPTURE_CONSUMER_GROUP_ID (the
# run-scoped base every RunScopedCapture attempt derives its own group
# from). One router process holds ONE group for its whole continuous
# lifetime, covering many RobotRuns at once; a RunScopedCapture holds
# one fresh group PER RobotRun. The two paths must never share
# committed-offset state, so they never share a group namespace either.
ROUTER_CONSUMER_GROUP_ID = "sceneops-mcap-continuous-router"

# Per-message errors that mean "this ONE RobotRun's stream is corrupt
# or violates an invariant" -- never "the router itself is broken."
# Caught and isolated per-session (see ContinuousCaptureRouter._route);
# every other session keeps being served normally.
_PER_RUN_ERRORS = (PartitionInvariantError, SequenceIntegrityError, UnsupportedChannelError)


class SessionState(StrEnum):
    """``DISCOVERED`` -- a session exists (explicit ``RUN_START`` seen,
    or implicitly created by that run's first telemetry record) but has
    not yet had any telemetry successfully written.
    ``RECORDING`` -- at least one telemetry record has been written.
    ``FINALIZING`` -- transient: finalize I/O (validate/atomic-rename)
    is in progress. Observable in principle, vanishingly short in
    practice (single-threaded, synchronous file I/O).
    ``FINALIZED`` -- terminal, successful. ``result`` may still be
    ``None`` if the session finalized with zero telemetry ever written
    (e.g. ``RUN_START`` immediately followed by ``RUN_END``) -- there
    is no file to describe in that case.
    ``FAILED`` -- terminal, unsuccessful (sequence/partition/writer
    invariant violation, or a finalize-time I/O failure). Data is
    discarded, never finalized, never silently repaired."""

    DISCOVERED = "discovered"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    FAILED = "failed"


_NON_TERMINAL_STATES = (SessionState.DISCOVERED, SessionState.RECORDING, SessionState.FINALIZING)
_TERMINAL_STATES = (SessionState.FINALIZED, SessionState.FAILED)


class FinalizationReason(StrEnum):
    """Why a session transitioned to ``FINALIZED`` -- recorded, never
    inferred after the fact, so a caller/operator can always tell a
    clean explicit end from a defensive timeout."""

    EXPLICIT_RUN_END = "explicit_run_end"
    IDLE_TIMEOUT = "idle_timeout"
    MANUAL = "manual"  # caller-driven finalize_run()/finalize_all(), no RUN_END seen


class MaxActiveRunsExceededError(RuntimeError):
    """The router already has ``max_active_runs`` non-terminal sessions
    open and refuses to silently evict one to make room for a new one
    -- fail loudly; eviction policy beyond idle-timeout is not invented
    here."""


class RunIdentityConflictError(RuntimeError):
    """Two records shared the same ``robot_run_id`` but disagreed on
    ``robot_id`` -- ``robot_run_id`` is supposed to be a globally unique
    identity; this is a data-integrity violation, never silently
    resolved by picking one side."""


class UnknownRunError(RuntimeError):
    """``finalize_run()`` was asked to finalize a ``robot_run_id`` with
    no non-terminal session (never seen, already finalized, or already
    failed)."""


@dataclass(frozen=True)
class CaptureSessionState:
    """Read-only snapshot of one session's runtime state -- execution/
    runtime state (Phase 7.0 study §9), never a canonical domain
    record. Mirrors ``CaptureResult``'s field set plus the
    execution-only fields (``state``, ``opened_at``,
    ``last_activity_at``, ``finalization_reason``, ``failure``) a
    finished ``CaptureResult`` alone cannot express."""

    robot_id: str
    robot_run_id: str
    state: SessionState
    opened_at: float
    last_activity_at: float
    message_count: int
    partition: int | None
    first_offset: int | None
    last_offset: int | None
    first_sequence: int | None
    last_sequence: int | None
    finalization_reason: FinalizationReason | None
    failure: str | None


class CaptureSession:
    """Internal, mutable per-RobotRun runtime state -- owns exactly one
    ``McapCaptureWriter`` (one open file), one ``_RunFilter`` (partition
    invariant + robot_id/robot_run_id identity check, reused unmodified
    from ``capture_consumer``), and one ``_SequenceTracker`` (reused
    unmodified) so every session's duplicate/gap/conflict handling is
    exactly as strict, and exactly as independent, as a standalone
    ``run_capture()`` invocation's always was.

    ``last_activity_at`` is updated ONLY from the router's own
    injectable clock (``time.monotonic`` by default), NEVER from
    ``envelope.source_timestamp_ns`` or ``envelope.ingest_timestamp_ns``
    -- both of those are the PRODUCER's timestamps, which can be
    arbitrarily stale relative to when the router actually processes a
    record (e.g. while catching up on real Kafka backlog, exactly the
    scenario Phase 7.0 measured at length). Using either for
    idle-timeout decisions would make backlog catch-up look like every
    session has been "idle" for however far behind the router is,
    causing false-positive timeouts during the one workload
    (historical replay/backlog) this transport is explicitly built to
    handle. ``last_activity_at`` answers "how long has it actually been,
    in wall-clock reality, since the router itself last heard from this
    run" -- the only question an idle-timeout fallback should be
    asking."""

    def __init__(
        self,
        *,
        robot_id: str,
        robot_run_id: str,
        output_root: Path,
        clock: Callable[[], float],
        registry: ChannelRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self.robot_id = robot_id
        self.robot_run_id = robot_run_id
        self._clock = clock
        partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
        self.writer = McapCaptureWriter(bag_uri=str(partial_dir), registry=registry)
        self.run_filter = capture_consumer._RunFilter(
            robot_id=robot_id, robot_run_id=robot_run_id
        )
        self.tracker = capture_consumer._SequenceTracker()
        self.first_offset: int | None = None
        self.last_offset: int | None = None
        now = clock()
        self.opened_at = now
        self.last_activity_at = now
        self.state = SessionState.DISCOVERED
        self.finalization_reason: FinalizationReason | None = None
        self.failure: str | None = None
        self.result: CaptureResult | None = None

    def touch(self) -> None:
        self.last_activity_at = self._clock()

    def snapshot(self) -> CaptureSessionState:
        return CaptureSessionState(
            robot_id=self.robot_id,
            robot_run_id=self.robot_run_id,
            state=self.state,
            opened_at=self.opened_at,
            last_activity_at=self.last_activity_at,
            message_count=self.writer.stats.message_count,
            partition=self.run_filter.partition,
            first_offset=self.first_offset,
            last_offset=self.last_offset,
            first_sequence=self.tracker.first_sequence,
            last_sequence=self.tracker.last_sequence,
            finalization_reason=self.finalization_reason,
            failure=self.failure,
        )


@dataclass
class _RouterStats:
    messages_routed: int = 0
    messages_written: int = 0
    messages_skipped_duplicate: int = 0
    messages_after_finalization: int = 0
    poison_messages: list[dict] = field(default_factory=list)
    control_events_ignored: list[dict] = field(default_factory=list)


class ContinuousCaptureRouter:
    """One continuous Kafka consumer, routing to many concurrently-open,
    independently-tracked per-RobotRun ``CaptureSession``s.

    **Kafka offset-commit correctness.** A single continuous consumer
    group can only commit ONE position per partition, but serves many
    downstream "consumers" (one open MCAP writer per non-terminal
    session) that reach their own durability boundary at different
    times. Never commit a partition's offset past the earliest
    first-consumed-offset of any session still NON-TERMINAL
    (``DISCOVERED``/``RECORDING``/``FINALIZING``) on that partition::

        min(session.first_offset for session in non-terminal sessions
            on this partition)
        -- if any, else
        (last_consumed_offset_on_this_partition + 1)
        -- once every session ever opened on it has reached a terminal
           state (FINALIZED or FAILED)

    Monotonically non-decreasing: a session leaving the non-terminal
    set (finalized OR failed -- both are terminal) only ever removes
    its `first_offset` from the `min(...)`, which can only raise the
    bound. This is what makes §5's "offset frontier release" property
    hold regardless of WHICH lifecycle path (explicit ``RUN_END``,
    idle-timeout, or a failure) is what moved a session out of the
    non-terminal set -- the commit-safety math only cares that it did,
    not why.

    **Per-session failure isolation.** A ``PartitionInvariantError``/
    ``SequenceIntegrityError``/``UnsupportedChannelError`` for one
    session's stream is caught, that session's writer is closed and its
    ``.partial`` state discarded (never finalized), the session
    transitions to ``FAILED``, and the router keeps serving every OTHER
    session unaffected. A FAILED session is permanently terminal --
    later telemetry/control events for the same ``robot_run_id`` are
    dropped, recorded, never silently reopened; recovering it is what
    ``RunScopedCapture`` (replay/backfill) remains for.

    **Poison (undecodable) records.** A record that fails to DECODE at
    all (``EnvelopeDecodeError``, no reliable ``robot_run_id``) cannot
    be attributed to any one session, so it is recorded in
    ``stats.poison_messages`` (topic/partition/offset/error, never
    silently dropped) and the loop continues -- letting it abort every
    currently-open session (this module's blast radius is much larger
    than ``run_capture()``'s single-run one) would be a worse outcome.
    Its own offset still counts toward that partition's
    ``_last_consumed_offset_by_partition`` bookkeeping (it WAS
    examined), so it can become safely committed exactly like any other
    consumed-but-not-attributable-to-an-open-session record: once no
    non-terminal session's `first_offset` sits at or before it.
    """

    def __init__(
        self,
        *,
        settings: StreamingSettings,
        output_root: Path,
        max_active_runs: int = 64,
        group_id: str | None = None,
        poll_timeout_seconds: float = 1.0,
        session_idle_timeout_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        registry: ChannelRegistry = DEFAULT_REGISTRY,
    ) -> None:
        """``session_idle_timeout_seconds`` -- defensive fallback (§3):
        a non-terminal session whose ``last_activity_at`` is this many
        clock-seconds in the past is automatically finalized through
        the SAME durable path an explicit ``RUN_END`` uses (never
        discarded), with ``finalization_reason=IDLE_TIMEOUT`` recorded.
        ``None`` (default) disables it entirely -- existing callers that
        never configure it get exactly Phase 7.1's behavior (sessions
        stay open until explicitly finalized).

        ``clock`` -- injectable time source (``time.monotonic`` by
        default) used for ``opened_at``/``last_activity_at``/idle-
        timeout comparisons. Tests inject a fake, deterministic clock
        instead of sleeping."""
        self._settings = settings
        self._output_root = output_root
        self._max_active_runs = max_active_runs
        self._poll_timeout_seconds = poll_timeout_seconds
        self._session_idle_timeout_seconds = session_idle_timeout_seconds
        self._clock = clock
        self._registry = registry

        self._sessions: dict[str, CaptureSession] = {}
        self._last_consumed_offset_by_partition: dict[int, int] = {}
        self.stats = _RouterStats()

        self._consumer = KafkaTelemetryConsumer(
            settings=settings,
            group_id=group_id or ROUTER_CONSUMER_GROUP_ID,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )

    # -- introspection -----------------------------------------------

    @property
    def active_run_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.state in _NON_TERMINAL_STATES)

    def session_states(self) -> dict[str, CaptureSessionState]:
        """Every session this router has ever seen, terminal or not."""
        return {run_id: s.snapshot() for run_id, s in self._sessions.items()}

    def active_run_states(self) -> dict[str, CaptureSessionState]:
        """Non-terminal sessions only -- convenience filter over
        ``session_states()``."""
        return {
            run_id: s.snapshot()
            for run_id, s in self._sessions.items()
            if s.state in _NON_TERMINAL_STATES
        }

    @property
    def finalized_runs(self) -> dict[str, CaptureResult]:
        return {
            run_id: s.result
            for run_id, s in self._sessions.items()
            if s.state is SessionState.FINALIZED and s.result is not None
        }

    @property
    def failed_runs(self) -> dict[str, str]:
        return {
            run_id: (s.failure or "")
            for run_id, s in self._sessions.items()
            if s.state is SessionState.FAILED
        }

    # -- consumption ---------------------------------------------------

    async def run_once(self, timeout_seconds: float | None = None) -> bool:
        """Poll once; route at most one message, then check idle
        sessions. Returns ``True`` if a message was consumed (whether
        written, skipped as a duplicate, a control event, or recorded
        as a per-session/poison failure), ``False`` on timeout with
        nothing available. Idle-timeout checking (§3) runs on EVERY
        call, including timeout/no-message ones, so a fallback
        finalization fires even against a quiet topic."""
        try:
            consumed = await self._consumer.poll(
                timeout_seconds if timeout_seconds is not None else self._poll_timeout_seconds
            )
        except EnvelopeDecodeError as exc:
            if exc.partition is not None and exc.offset is not None:
                self._last_consumed_offset_by_partition[exc.partition] = exc.offset
            self.stats.poison_messages.append(
                {
                    "topic": exc.topic,
                    "partition": exc.partition,
                    "offset": exc.offset,
                    "error": str(exc),
                }
            )
            await self.check_idle_sessions()
            return True

        if consumed is None:
            await self.check_idle_sessions()
            return False

        await self._route(consumed, receive_time_ns=time.time_ns())
        await self.check_idle_sessions()
        return True

    async def run_for(
        self,
        *,
        max_messages: int | None = None,
        loop_idle_timeout_seconds: float = 5.0,
        poll_timeout_seconds: float | None = None,
    ) -> int:
        """Convenience driving loop for tests/benchmarks/a real
        long-lived session: keep polling until ``max_messages`` have
        been consumed (if given) or no message arrives for
        ``loop_idle_timeout_seconds`` (the LOOP's own "give up polling"
        timeout -- distinct from ``session_idle_timeout_seconds``,
        which finalizes one stale SESSION while the loop keeps running).
        Returns the number of messages consumed. Not itself a
        production lifecycle policy -- a real continuous deployment
        drives ``run_once()`` in its own loop under whatever supervision
        it needs (restart/rebalance recovery, Phase 7.4)."""
        consumed_count = 0
        last_progress = time.monotonic()
        while max_messages is None or consumed_count < max_messages:
            got = await self.run_once(poll_timeout_seconds)
            if got:
                consumed_count += 1
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress > loop_idle_timeout_seconds:
                break
        return consumed_count

    async def _route(
        self, consumed: ConsumedTelemetryEnvelope, *, receive_time_ns: int
    ) -> None:
        envelope = consumed.envelope
        robot_run_id = envelope.robot_run_id
        partition = consumed.partition
        self._last_consumed_offset_by_partition[partition] = consumed.offset
        self.stats.messages_routed += 1

        if is_control_envelope(envelope):
            await self._handle_control_event(robot_run_id, envelope)
            return

        session = self._sessions.get(robot_run_id)
        if session is not None and session.state in _TERMINAL_STATES:
            self.stats.messages_after_finalization += 1
            return

        if session is None:
            session = self._new_session(robot_run_id, envelope.robot_id)

        session.touch()
        try:
            if not session.run_filter.matches(consumed):
                raise RunIdentityConflictError(
                    f"robot_run_id={robot_run_id!r}: message robot_id="
                    f"{envelope.robot_id!r} does not match this session's "
                    f"established robot_id={session.robot_id!r}"
                )
            should_write = session.tracker.accept(
                sequence_number=envelope.sequence_number, payload=envelope.payload
            )
            if should_write:
                session.writer.write_envelope(
                    envelope, receive_time_ns=receive_time_ns
                )
                self.stats.messages_written += 1
                if session.state is SessionState.DISCOVERED:
                    session.state = SessionState.RECORDING
            else:
                self.stats.messages_skipped_duplicate += 1
            if session.first_offset is None:
                session.first_offset = consumed.offset
            session.last_offset = consumed.offset
        except (*_PER_RUN_ERRORS, RunIdentityConflictError) as exc:
            self._mark_failed(session, exc)

    def _new_session(self, robot_run_id: str, robot_id: str) -> CaptureSession:
        if self.active_run_count >= self._max_active_runs:
            raise MaxActiveRunsExceededError(
                f"cannot open a new session for {robot_run_id!r}: "
                f"max_active_runs={self._max_active_runs} already reached "
                f"({sorted(self.active_run_states())!r})"
            )
        session = CaptureSession(
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            output_root=self._output_root,
            clock=self._clock,
            registry=self._registry,
        )
        self._sessions[robot_run_id] = session
        return session

    def _mark_failed(self, session: CaptureSession, exc: Exception) -> None:
        try:
            session.writer.close()
        except Exception:
            pass  # best-effort -- already failing this session over exc
        shutil.rmtree(
            partial_bag_path(self._output_root, session.robot_run_id), ignore_errors=True
        )
        session.state = SessionState.FAILED
        session.failure = f"{type(exc).__name__}: {exc}"

    # -- control events (RUN_START / RUN_END) ---------------------------

    async def _handle_control_event(self, robot_run_id: str, envelope) -> None:
        event_type = parse_run_event(envelope)
        session = self._sessions.get(robot_run_id)

        if event_type is RunEventType.RUN_START:
            if session is not None:
                # Redelivery (at-least-once) or a genuine duplicate --
                # idempotent no-op regardless of the existing session's
                # state; never resets an in-progress or terminal session.
                self.stats.control_events_ignored.append(
                    {"robot_run_id": robot_run_id, "event": "RUN_START", "reason": "duplicate"}
                )
                return
            self._new_session(robot_run_id, envelope.robot_id)
            return

        if event_type is RunEventType.RUN_END:
            if session is None:
                # No prior telemetry or RUN_START at all -- nothing to
                # finalize; recorded, not silently dropped.
                self.stats.control_events_ignored.append(
                    {"robot_run_id": robot_run_id, "event": "RUN_END", "reason": "unknown_run"}
                )
                return
            if session.state not in _NON_TERMINAL_STATES:
                # Duplicate RUN_END, or racing an idle-timeout that
                # already finalized/failed this session first -- the
                # natural, deterministic race resolution: whichever
                # terminalizes the session first wins, the other is a
                # harmless no-op.
                self.stats.control_events_ignored.append(
                    {
                        "robot_run_id": robot_run_id,
                        "event": "RUN_END",
                        "reason": f"already_{session.state.value}",
                    }
                )
                return
            try:
                self._finalize_session_io(session, reason=FinalizationReason.EXPLICIT_RUN_END)
            except Exception:
                pass  # already recorded as FAILED inside _finalize_session_io
            await self.commit_safe()
            return

        # Unrecognized message_type under the control channel --
        # forward-compatible: ignore, never fatal (control.py's own
        # parse_run_event docstring).
        self.stats.control_events_ignored.append(
            {
                "robot_run_id": robot_run_id,
                "event": envelope.message_type,
                "reason": "unrecognized_control_event",
            }
        )

    # -- idle-timeout fallback ------------------------------------------

    async def check_idle_sessions(self) -> list[str]:
        """Finalize every non-terminal session whose ``last_activity_at``
        is >= ``session_idle_timeout_seconds`` in the past, through the
        same durable finalize path ``RUN_END`` uses (never discards
        data). Returns the ``robot_run_id``s finalized this way. A
        no-op (returns ``[]``) if ``session_idle_timeout_seconds`` was
        never configured."""
        if self._session_idle_timeout_seconds is None:
            return []
        now = self._clock()
        timed_out: list[str] = []
        for robot_run_id, session in list(self._sessions.items()):
            if session.state not in _NON_TERMINAL_STATES:
                continue
            if now - session.last_activity_at < self._session_idle_timeout_seconds:
                continue
            try:
                self._finalize_session_io(session, reason=FinalizationReason.IDLE_TIMEOUT)
            except Exception:
                pass  # already recorded as FAILED inside _finalize_session_io
            timed_out.append(robot_run_id)
        if timed_out:
            await self.commit_safe()
        return timed_out

    # -- finalization ---------------------------------------------------

    def _finalize_session_io(
        self, session: CaptureSession, *, reason: FinalizationReason
    ) -> CaptureResult | None:
        """Validate/finalize one session's MCAP and transition its
        state -- always ends in ``FINALIZED`` (success, possibly with
        ``result=None`` if zero telemetry was ever written) or
        ``FAILED`` (this method re-raises on failure; every caller of
        this method is responsible for deciding whether that should
        propagate further or be swallowed, see call sites). Never
        leaves a session stuck in ``FINALIZING`` or silently untracked
        -- the Phase 7.1 gap this phase's own audit found (a finalize
        I/O failure used to leave a session popped from tracking with
        the exception simply propagating, effectively losing it)."""
        session.state = SessionState.FINALIZING
        try:
            session.writer.close()
            if session.writer.stats.message_count == 0:
                # RUN_START (or first telemetry) followed immediately by
                # RUN_END/idle-timeout with nothing captured in between
                # -- validate_mcap_file would reject a zero-message file
                # anyway; there is nothing to finalize, just discard the
                # empty writer directory.
                shutil.rmtree(
                    partial_bag_path(self._output_root, session.robot_run_id),
                    ignore_errors=True,
                )
                session.state = SessionState.FINALIZED
                session.finalization_reason = reason
                session.result = None
                return None

            mcap_path = session.writer.mcap_file_path()
            validate_mcap_file(
                mcap_path, expected_message_count=session.writer.stats.message_count
            )
            final_dir = finalize_bag(self._output_root, session.robot_run_id)
            final_mcap_path = final_dir / Path(mcap_path).name
            digest_hex = capture_consumer._sha256_file(final_mcap_path)
            result = CaptureResult(
                robot_id=session.robot_id,
                robot_run_id=session.robot_run_id,
                path=final_mcap_path,
                message_count=session.writer.stats.message_count,
                partition=session.run_filter.partition,
                first_offset=session.first_offset,
                last_offset=session.last_offset,
                first_sequence=session.tracker.first_sequence,
                last_sequence=session.tracker.last_sequence,
                sha256=digest_hex,
                per_channel_counts=dict(session.writer.stats.per_channel_counts),
            )
        except Exception as exc:
            session.state = SessionState.FAILED
            session.failure = f"{type(exc).__name__}: {exc}"
            shutil.rmtree(
                partial_bag_path(self._output_root, session.robot_run_id), ignore_errors=True
            )
            raise

        session.state = SessionState.FINALIZED
        session.finalization_reason = reason
        session.result = result
        return result

    async def finalize_run(self, robot_run_id: str) -> CaptureResult | None:
        """Finalize exactly one non-terminal session -- validate/
        finalize/commit, reusing the same durability pipeline
        ``run_capture()`` uses, while every OTHER session stays
        untouched. Caller-driven (``finalization_reason=MANUAL``):
        unlike the control-event/idle-timeout paths, a finalize failure
        here PROPAGATES (the caller explicitly asked and should know),
        though the session itself is already correctly transitioned to
        ``FAILED`` by the time the exception reaches them, never left
        untracked."""
        session = self._sessions.get(robot_run_id)
        if session is None or session.state not in _NON_TERMINAL_STATES:
            raise UnknownRunError(
                f"no non-terminal session {robot_run_id!r} to finalize "
                f"(known={sorted(self._sessions)!r})"
            )
        try:
            result = self._finalize_session_io(session, reason=FinalizationReason.MANUAL)
        finally:
            await self.commit_safe()
        return result

    async def finalize_all(self) -> dict[str, CaptureResult]:
        """Finalize every currently non-terminal session, then commit
        once. A single session's finalize failure does not stop the
        rest (it is recorded as FAILED internally by
        ``_finalize_session_io`` and skipped in the returned mapping,
        matching the per-session isolation principle used everywhere
        else in this router)."""
        results: dict[str, CaptureResult] = {}
        for robot_run_id, session in list(self._sessions.items()):
            if session.state not in _NON_TERMINAL_STATES:
                continue
            try:
                result = self._finalize_session_io(session, reason=FinalizationReason.MANUAL)
            except Exception:
                continue  # already recorded as FAILED
            if result is not None:
                results[robot_run_id] = result
        await self.commit_safe()
        return results

    # -- Kafka offset-commit safety -------------------------------------

    def _safe_commit_offsets(self) -> dict[int, int]:
        safe: dict[int, int] = {}
        non_terminal_firsts_by_partition: dict[int, list[int]] = {}
        for session in self._sessions.values():
            if session.state not in _NON_TERMINAL_STATES:
                continue
            partition = session.run_filter.partition
            if partition is None or session.first_offset is None:
                continue  # this session has consumed nothing yet -- nothing to bound by
            non_terminal_firsts_by_partition.setdefault(partition, []).append(
                session.first_offset
            )

        for partition, last_offset in self._last_consumed_offset_by_partition.items():
            firsts = non_terminal_firsts_by_partition.get(partition)
            safe[partition] = min(firsts) if firsts else last_offset + 1
        return safe

    async def commit_safe(self) -> None:
        """Commit every partition this router has consumed from up to
        the current safe boundary (see class docstring). Idempotent --
        calling it with nothing new to advance is harmless."""
        await self._consumer.commit_offsets(self._safe_commit_offsets())

    # -- shutdown ---------------------------------------------------

    async def close(self) -> None:
        """Release the underlying Kafka consumer only. Does NOT
        finalize any non-terminal session -- finalization is always an
        explicit (control event, idle-timeout, or caller-driven)
        decision, never implied by shutdown. Any session left
        non-terminal when ``close()`` is called keeps its `.partial`
        state on disk, un-finalized; because of the safe-commit policy
        above, none of its already-consumed messages were ever
        committed past, so a fresh router (or a `RunScopedCapture`
        backfill) can always recover it from Kafka."""
        await self._consumer.close()

    async def __aenter__(self) -> "ContinuousCaptureRouter":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
