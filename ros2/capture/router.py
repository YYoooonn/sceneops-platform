"""Continuous multi-RobotRun capture router (Phase 7.1).

One long-lived Kafka consumer, routing each consumed record by
``TelemetryEnvelope.robot_run_id`` to that run's own, independently
sequence-tracked, independently written MCAP -- replacing N independent
full-topic-history rescans (one per ``run_capture()`` invocation, the
existing ``RunScopedCapture`` path in ``capture_consumer.py``) with one
continuous topic pass serving arbitrarily many concurrent RobotRuns.

This module composes ``capture_consumer.py``'s own building blocks
(``_RunFilter``, ``_SequenceTracker``, ``_sha256_file``) and
``finalize.py``/``validation.py``/``mcap_writer.py`` directly -- it does
not reimplement any of them, and it does not modify them. The frozen
commit-boundary ordering they encode (write -> close/fsync -> validate
-> atomically finalize -> only then advance Kafka position) is
preserved per-run; see ``ContinuousCaptureRouter``'s own docstring for
how that ordering composes across MANY simultaneously-open runs, which
is genuinely new here (``run_capture()`` only ever had one run open at
a time).

``RunScopedCapture`` (``capture_consumer.run_capture``) is untouched and
remains the supported path for replay/backfill/debugging/recovery --
this module is an additional, independent consumer of the same frozen
Kafka/envelope/MCAP contracts, not a replacement.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sceneops_core.streaming import ConsumedTelemetryEnvelope
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
# Caught and isolated per-run (see ContinuousCaptureRouter._route);
# every other active run keeps being served normally.
_PER_RUN_ERRORS = (PartitionInvariantError, SequenceIntegrityError, UnsupportedChannelError)


class MaxActiveRunsExceededError(RuntimeError):
    """The router already has ``max_active_runs`` RobotRuns open and
    refuses to silently evict one to make room for a new one -- fail
    loudly; eviction/idle-timeout policy is explicitly Phase 7.2 scope,
    not invented here."""


class RunIdentityConflictError(RuntimeError):
    """Two records shared the same ``robot_run_id`` but disagreed on
    ``robot_id`` -- ``robot_run_id`` is supposed to be a globally unique
    identity; this is a data-integrity violation, never silently
    resolved by picking one side."""


class UnknownRunError(RuntimeError):
    """``finalize_run()`` was asked to finalize a ``robot_run_id`` that
    is not currently active (never seen, already finalized, or already
    abandoned after a per-run failure)."""


@dataclass(frozen=True)
class ActiveRunState:
    """Read-only snapshot of one currently-open run's runtime state --
    execution/runtime state (Phase 7.0 study §9), never a canonical
    domain record. Mirrors ``CaptureResult``'s field set plus the
    execution-only fields (``opened_monotonic``) a still-open run has
    and a finished ``CaptureResult`` does not."""

    robot_id: str
    robot_run_id: str
    partition: int | None
    message_count: int
    first_offset: int | None
    last_offset: int | None
    first_sequence: int | None
    last_sequence: int | None
    opened_monotonic: float


class _ActiveRun:
    """Internal, mutable per-run runtime state -- owns exactly one
    ``McapCaptureWriter`` (one open file), one ``_RunFilter`` (partition
    invariant + robot_id/robot_run_id identity check, reused unmodified
    from ``capture_consumer``), and one ``_SequenceTracker`` (reused
    unmodified) so every active run's duplicate/gap/conflict handling
    is exactly as strict, and exactly as independent, as a standalone
    ``run_capture()`` invocation's always was."""

    def __init__(self, *, robot_id: str, robot_run_id: str, output_root: Path) -> None:
        self.robot_id = robot_id
        self.robot_run_id = robot_run_id
        partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
        self.writer = McapCaptureWriter(bag_uri=str(partial_dir))
        self.run_filter = capture_consumer._RunFilter(
            robot_id=robot_id, robot_run_id=robot_run_id
        )
        self.tracker = capture_consumer._SequenceTracker()
        self.first_offset: int | None = None
        self.last_offset: int | None = None
        self.opened_monotonic = time.monotonic()

    def state(self) -> ActiveRunState:
        return ActiveRunState(
            robot_id=self.robot_id,
            robot_run_id=self.robot_run_id,
            partition=self.run_filter.partition,
            message_count=self.writer.stats.message_count,
            first_offset=self.first_offset,
            last_offset=self.last_offset,
            first_sequence=self.tracker.first_sequence,
            last_sequence=self.tracker.last_sequence,
            opened_monotonic=self.opened_monotonic,
        )


@dataclass
class _RouterStats:
    messages_routed: int = 0
    messages_written: int = 0
    messages_skipped_duplicate: int = 0
    poison_messages: list[dict] = field(default_factory=list)


class ContinuousCaptureRouter:
    """One continuous Kafka consumer, routing to many concurrently-open,
    independently-tracked per-RobotRun MCAP writers.

    **Kafka offset-commit correctness (the central question this phase
    exists to answer).** A single continuous consumer group can only
    commit ONE position per partition -- but it is serving many
    downstream "consumers" (one open MCAP writer per active RobotRun)
    that reach their own durability boundary (finalized-and-validated)
    at different times. Naively committing "the most recently consumed
    record" (``run_capture()``'s own, correct-for-ITS-case policy, since
    it only ever has ONE run open) would be WRONG here: if record R for
    still-open run B was consumed and the committed offset advanced
    past R, then the process crashes before B ever finalizes, restarting
    a fresh consumer in the same group resumes from a position that
    already skips R -- run B's data is gone, un-redeliverable, and
    B was never durably written anywhere. That is a real durability
    violation, not a cosmetic one.

    The policy implemented here is the conservative one the Phase 7.1
    brief asks for when full recovery infrastructure (Phase 7.2/7.4) is
    not yet built: **never commit a partition's offset past the
    earliest first-consumed-offset of any RobotRun still active on that
    partition.** Concretely, per partition, the safe "next offset to
    read" is::

        min(run.first_offset for run in active_runs on this partition)
        -- if any run is active on it, else
        (last_consumed_offset_on_this_partition + 1)
        -- once every run ever opened on it has been finalized (or
           abandoned, see below)

    This is monotonically non-decreasing (finalizing/abandoning a run
    only ever removes its `first_offset` from the `min(...)`, which can
    only raise the bound; a brand-new run's `first_offset` is always
    >= the current consume position, which is always >= every prior
    run's `first_offset`) -- so the safe boundary only ever advances,
    never regresses, as runs complete. It is called automatically after
    every ``finalize_run``/``finalize_all`` call, and may be called
    directly (``commit_safe()``) at any other point a caller wants to
    checkpoint progress.

    **What this policy does NOT do (explicitly deferred, matching the
    brief's scope exclusions):** it does not attempt to recover an
    in-flight, not-yet-finalized run's `.partial` state after a process
    restart -- a restart's fresh router simply starts consuming again
    from the last safely-committed position, and Kafka redelivers
    everything from there, including a full replay of whatever any
    still-open run at crash time had already (uncommittedly) consumed.
    Full restart recovery, idle-timeout-driven auto-finalization, and
    Kafka-rebalance recovery are Phase 7.2/7.4 scope, not this module's.

    **Per-run failure isolation.** A ``PartitionInvariantError``/
    ``SequenceIntegrityError``/``UnsupportedChannelError`` for one
    active run's stream (this run's own data violates an invariant --
    never a router-level problem) is caught, that ONE run's writer is
    closed and its ``.partial`` state discarded (never finalized, never
    silently repaired), and the router keeps serving every OTHER active
    run unaffected. The failed ``robot_run_id`` is permanently ignored
    for the remainder of this router's lifetime (recorded in
    ``failed_runs``) -- a later record for the same id is dropped
    rather than silently reopening a run whose invariants already broke
    once; recovering it is what ``RunScopedCapture`` (replay/backfill)
    remains for.

    A record that fails to DECODE at all (``EnvelopeDecodeError`` from
    the underlying ``KafkaTelemetryConsumer.poll()`` itself, e.g. a
    corrupt/malformed Kafka record with no reliable ``robot_run_id`` to
    attribute it to) is a deliberate exception to "never silently drop
    malformed records": it cannot be attributed to any one run, so
    isolating it the way a per-run error is isolated is not possible,
    and letting ONE poison record abort ingestion for every currently
    active run (this module's blast radius is much larger than
    ``run_capture()``'s single-run one) is a worse outcome than
    recording it and continuing. It is never silently lost -- every
    poison record is appended to ``poison_messages`` (topic/partition/
    offset/error), queryable by any caller, just not raised
    synchronously into the poll loop.
    """

    def __init__(
        self,
        *,
        settings: StreamingSettings,
        output_root: Path,
        max_active_runs: int = 64,
        group_id: str | None = None,
        poll_timeout_seconds: float = 1.0,
    ) -> None:
        self._settings = settings
        self._output_root = output_root
        self._max_active_runs = max_active_runs
        self._poll_timeout_seconds = poll_timeout_seconds

        self._active: dict[str, _ActiveRun] = {}
        self._finalized: dict[str, CaptureResult] = {}
        self._failed: dict[str, str] = {}
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
        return len(self._active)

    def active_run_states(self) -> dict[str, ActiveRunState]:
        return {run_id: run.state() for run_id, run in self._active.items()}

    @property
    def finalized_runs(self) -> dict[str, CaptureResult]:
        return dict(self._finalized)

    @property
    def failed_runs(self) -> dict[str, str]:
        return dict(self._failed)

    # -- consumption ---------------------------------------------------

    async def run_once(self, timeout_seconds: float | None = None) -> bool:
        """Poll once; route and write at most one message. Returns
        ``True`` if a message was consumed (whether written, skipped as
        a duplicate, or recorded as a per-run/poison failure), ``False``
        on timeout with nothing available.

        A record that fails to DECODE at all (``EnvelopeDecodeError``,
        raised by the underlying ``KafkaTelemetryConsumer.poll()``
        itself) has no reliable ``robot_run_id`` to attribute it to, so
        it cannot be isolated the way a per-run failure is (§ class
        docstring) -- it is caught here, appended to
        ``stats.poison_messages`` (topic/partition/offset/error, never
        silently discarded), and the loop continues. The record's own
        offset still counts as "consumed" for this partition's safe-
        commit bookkeeping (it WAS examined; the router is simply
        unable to route it to any run's writer)."""
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
            return True
        if consumed is None:
            return False
        self._route(consumed)
        return True

    async def run_for(
        self,
        *,
        max_messages: int | None = None,
        idle_timeout_seconds: float = 5.0,
        poll_timeout_seconds: float | None = None,
    ) -> int:
        """Convenience driving loop for tests/benchmarks/a real
        long-lived session: keep polling until ``max_messages`` have
        been consumed (if given) or no message arrives for
        ``idle_timeout_seconds``. Returns the number of messages
        consumed. Not itself a production lifecycle policy -- a real
        continuous deployment drives ``run_once()`` in its own loop
        under whatever supervision it needs (Phase 7.2)."""
        consumed_count = 0
        last_progress = time.monotonic()
        while max_messages is None or consumed_count < max_messages:
            got = await self.run_once(poll_timeout_seconds)
            if got:
                consumed_count += 1
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress > idle_timeout_seconds:
                break
        return consumed_count

    def _route(self, consumed: ConsumedTelemetryEnvelope) -> None:
        envelope = consumed.envelope
        robot_run_id = envelope.robot_run_id
        partition = consumed.partition
        self._last_consumed_offset_by_partition[partition] = consumed.offset
        self.stats.messages_routed += 1

        if robot_run_id in self._failed:
            return

        run = self._active.get(robot_run_id)
        if run is None:
            if len(self._active) >= self._max_active_runs:
                raise MaxActiveRunsExceededError(
                    f"cannot open a new active run for {robot_run_id!r}: "
                    f"max_active_runs={self._max_active_runs} already reached "
                    f"({sorted(self._active)!r})"
                )
            run = _ActiveRun(
                robot_id=envelope.robot_id,
                robot_run_id=robot_run_id,
                output_root=self._output_root,
            )
            self._active[robot_run_id] = run

        try:
            if not run.run_filter.matches(consumed):
                raise RunIdentityConflictError(
                    f"robot_run_id={robot_run_id!r}: message robot_id="
                    f"{envelope.robot_id!r} does not match this run's "
                    f"established robot_id={run.robot_id!r}"
                )
            should_write = run.tracker.accept(
                sequence_number=envelope.sequence_number, payload=envelope.payload
            )
            if should_write:
                run.writer.write_envelope(envelope)
                self.stats.messages_written += 1
            else:
                self.stats.messages_skipped_duplicate += 1
            if run.first_offset is None:
                run.first_offset = consumed.offset
            run.last_offset = consumed.offset
        except (*_PER_RUN_ERRORS, RunIdentityConflictError) as exc:
            self._abandon_run(robot_run_id, exc)

    def _abandon_run(self, robot_run_id: str, exc: Exception) -> None:
        run = self._active.pop(robot_run_id, None)
        if run is not None:
            try:
                run.writer.close()
            except Exception:
                pass  # best-effort -- already abandoning this run due to exc
            shutil.rmtree(
                partial_bag_path(self._output_root, robot_run_id), ignore_errors=True
            )
        self._failed[robot_run_id] = f"{type(exc).__name__}: {exc}"

    # -- finalization ---------------------------------------------------

    def _finalize_active_run(self, run: _ActiveRun) -> CaptureResult:
        run.writer.close()
        mcap_path = run.writer.mcap_file_path()
        validate_mcap_file(mcap_path, expected_message_count=run.writer.stats.message_count)
        final_dir = finalize_bag(self._output_root, run.robot_run_id)
        final_mcap_path = final_dir / Path(mcap_path).name
        digest_hex = capture_consumer._sha256_file(final_mcap_path)
        return CaptureResult(
            robot_id=run.robot_id,
            robot_run_id=run.robot_run_id,
            path=final_mcap_path,
            message_count=run.writer.stats.message_count,
            partition=run.run_filter.partition,
            first_offset=run.first_offset,
            last_offset=run.last_offset,
            first_sequence=run.tracker.first_sequence,
            last_sequence=run.tracker.last_sequence,
            sha256=digest_hex,
        )

    async def finalize_run(self, robot_run_id: str) -> CaptureResult:
        """Finalize exactly one active run -- validate/finalize/commit,
        reusing the same durability pipeline ``run_capture()`` uses,
        while every OTHER currently-active run stays open and
        untouched. Advances the safe commit boundary afterward."""
        run = self._active.pop(robot_run_id, None)
        if run is None:
            raise UnknownRunError(
                f"no active run {robot_run_id!r} to finalize "
                f"(active={sorted(self._active)!r}, "
                f"already finalized={robot_run_id in self._finalized}, "
                f"failed={robot_run_id in self._failed})"
            )
        result = self._finalize_active_run(run)
        self._finalized[robot_run_id] = result
        await self.commit_safe()
        return result

    async def finalize_all(self) -> dict[str, CaptureResult]:
        """Finalize every currently-active run, then commit once."""
        results: dict[str, CaptureResult] = {}
        for robot_run_id in list(self._active.keys()):
            run = self._active.pop(robot_run_id)
            result = self._finalize_active_run(run)
            self._finalized[robot_run_id] = result
            results[robot_run_id] = result
        await self.commit_safe()
        return results

    # -- Kafka offset-commit safety -------------------------------------

    def _safe_commit_offsets(self) -> dict[int, int]:
        safe: dict[int, int] = {}
        active_first_offsets_by_partition: dict[int, list[int]] = {}
        for run in self._active.values():
            partition = run.run_filter.partition
            if partition is None or run.first_offset is None:
                continue  # this run has consumed nothing yet -- nothing to bound by
            active_first_offsets_by_partition.setdefault(partition, []).append(
                run.first_offset
            )

        for partition, last_offset in self._last_consumed_offset_by_partition.items():
            active_firsts = active_first_offsets_by_partition.get(partition)
            safe[partition] = min(active_firsts) if active_firsts else last_offset + 1
        return safe

    async def commit_safe(self) -> None:
        """Commit every partition this router has consumed from up to
        the current safe boundary (see class docstring). Idempotent --
        calling it with nothing new to advance is harmless."""
        await self._consumer.commit_offsets(self._safe_commit_offsets())

    # -- shutdown ---------------------------------------------------

    async def close(self) -> None:
        """Release the underlying Kafka consumer only. Does NOT
        finalize any still-active run -- finalization is always an
        explicit, caller-driven decision (``finalize_run``/
        ``finalize_all``), never implied by shutdown. Any run left
        active when ``close()`` is called keeps its `.partial` state on
        disk, un-finalized; because of the safe-commit policy above,
        none of its already-consumed messages were ever committed past,
        so a fresh router (or a `RunScopedCapture` backfill) can always
        recover it from Kafka."""
        await self._consumer.close()

    async def __aenter__(self) -> "ContinuousCaptureRouter":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
