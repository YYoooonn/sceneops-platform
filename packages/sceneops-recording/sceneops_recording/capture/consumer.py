"""Orchestrates one run-scoped durable MCAP capture: Kafka -> RunFilter ->
SequenceTracker -> McapCaptureWriter -> validate -> finalize.

Frozen ordering (never reversed): consume -> write to the temp/partial MCAP
-> close the writer (fsync) -> validate by reading the file back -> write the
receipt -> atomically finalize (rename + fsync parent dir). The atomic finalize
is the only completion boundary: Capture reads Kafka and records no progress in
it. If anything before the finalize fails, this function raises, leaving the
partial bag in place for the next attempt to discard and rebuild from Kafka
(see ``finalize.py``).

Lifecycle is externally controlled: this module has no notion of what a
"mission" is and never inspects the content of ``/mission/status`` (or
any other channel) to decide when to start or stop capturing -- the
caller decides, via ``stop_condition``.

**Lifecycle control envelopes (Phase 7.2.1).** ``RUN_START``/``RUN_END``
(``sceneops_streaming.control``) share this run's Kafka key/
partition like any other record for it, so they pass ``_RunFilter``
same as telemetry -- but they are never sensor data: recognized via
``is_control_envelope`` and routed to their OWN ``_SequenceTracker``
(independent of the telemetry one), validated for gap/duplicate/
conflict exactly as strictly as telemetry is, but never passed to
``McapCaptureWriter.write_envelope`` (so they never appear in the
finalized MCAP, and never hit ``UnsupportedChannelError`` -- that
error is reserved for a genuinely unrecognized/unsupported channel,
not a recognized control one). A legacy stream with no control events
at all is completely unaffected: ``is_control_envelope`` is never true
for it, so every record takes the exact same path this module always
used.

A lifecycle-governed capture (``stop_on_run_end``) additionally enforces the
run's RUN_START -> telemetry -> RUN_END order (``lifecycle.py``): telemetry or
RUN_END before RUN_START and a second RUN_START fail like a sequence violation,
and a missing RUN_END is never finalized. RUN_END must declare the run's
``telemetry_count`` and Capture finalizes only if it observed exactly that many
telemetry records (``RunIncompleteError``). Without ``stop_on_run_end`` (count-
bounded synthetic runs) control events are only sequence-validated.

**Ownership.** One Capture attempt owns one RobotRun: a local lock on the run id
(``ownership.py``) is held from before ``.partial/<run>`` is touched until the
attempt is over, and with a ``KafkaStartPosition`` the attempt reads exactly one
explicitly assigned partition (no consumer-group join, no rebalance). Kafka
arbitrates nothing; which attempt captures a run is decided by whoever starts it.
A run's captured state is its finalized bag and receipt; an attempt neither
reads nor writes any consumer-group state.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sceneops_recording.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    FinalizationReason,
    ReceiptFinalization,
    ReceiptKafka,
    ReceiptRecording,
)
from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK
from sceneops_core.robots.manifest import (
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    RecordingFormat,
)
from sceneops_streaming import (
    DEFAULT_REGISTRY,
    ChannelRegistry,
    ConsumedTelemetryEnvelope,
    KafkaStartPosition,
    RunEndPayloadError,
    is_control_envelope,
    parse_run_end_telemetry_count,
    parse_run_event,
)
from sceneops_streaming.config import StreamingSettings
from sceneops_streaming.consumer import KafkaTelemetryConsumer

from .finalize import (
    FinalBagExistsError,
    final_bag_path,
    finalize_bag,
    prepare_partial_bag_dir,
)
from .lifecycle import RunLifecycle, RunLifecycleError, RunPhase, check_run_complete
from .ownership import own_run
from .writer import McapCaptureWriter
from .receipt_io import (
    CaptureReceiptConflictError,
    check_existing_receipt_converges,
    read_capture_receipt,
    write_capture_receipt,
)
from .validation import same_recorded_content, validate_mcap_file


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PartitionInvariantError(RuntimeError):
    """A robot_run_id's messages spanned more than one Kafka partition."""


class SequenceIntegrityError(RuntimeError):
    """A sequence gap, conflicting duplicate, or out-of-order redelivery
    was observed for this robot_run_id's stream."""


class RunEndNotObservedError(RuntimeError):
    """A capture that must end on its run's RUN_END was stopped before it
    arrived. What was consumed may be a truncated recording, so nothing is
    finalized."""


class RunStartNotObservedError(RuntimeError):
    """A capture that must start on its run's RUN_START was stopped before it
    arrived: the run never started on the topic (wrong ids, retention, a bridge
    that never published it), or the capture began after it. Nothing was
    recorded or finalized."""


@dataclass
class CaptureResult:
    robot_id: str
    robot_run_id: str
    path: Path
    message_count: int
    partition: int
    first_offset: int
    last_offset: int
    first_sequence: int
    last_sequence: int
    sha256: str
    per_channel_counts: dict[str, int] = field(default_factory=dict)
    # None only when converging onto a bag finalized without a receipt.
    receipt_path: Path | None = None


class _RunFilter:
    """Accepts only envelopes for the target robot_id/robot_run_id, and
    enforces that every accepted message comes from the same Kafka
    partition -- one robot_run_id must map to exactly one partition, and
    a violation fails loudly rather than silently merging streams."""

    def __init__(
        self, *, robot_id: str, robot_run_id: str, partition: int | None = None
    ) -> None:
        self._robot_id = robot_id
        self._robot_run_id = robot_run_id
        # Pre-set when the capture is pinned to a partition: a record of the
        # run from any other partition then fails like a later divergence.
        self._partition: int | None = partition

    def matches(self, consumed: ConsumedTelemetryEnvelope) -> bool:
        envelope = consumed.envelope
        if (
            envelope.robot_id != self._robot_id
            or envelope.robot_run_id != self._robot_run_id
        ):
            return False

        if self._partition is None:
            self._partition = consumed.partition
        elif consumed.partition != self._partition:
            raise PartitionInvariantError(
                f"robot_run_id={self._robot_run_id!r} spans multiple Kafka "
                f"partitions ({self._partition} and {consumed.partition}); "
                "one robot_run_id must map to exactly one partition"
            )
        return True

    @property
    def partition(self) -> int | None:
        return self._partition


class _SequenceTracker:
    """Enforces 0..N-1 sequence completeness for one robot_run_id's
    stream, with a bounded v1 duplicate policy:

    - exact immediate redelivery (same sequence number AND same payload
      as the last accepted message) -- skip, not an error (expected
      under at-least-once redelivery, e.g. a rebalance re-poll)
    - conflicting redelivery (same sequence number, different payload)
      -- fail
    - gap (sequence number greater than expected next) -- fail
    - late/out-of-order (less than expected next, and not the immediate
      redelivery case above) -- fail

    No unbounded dedup table -- only the single last-accepted
    (sequence, payload) pair is ever remembered.
    """

    def __init__(self) -> None:
        self._last_sequence: int | None = None
        self._last_payload: bytes | None = None
        self.first_sequence: int | None = None

    def accept(self, *, sequence_number: int, payload: bytes) -> bool:
        """Returns True if this message should be written, False if it
        is a duplicate to silently skip. Raises ``SequenceIntegrityError``
        for any other violation."""
        if self._last_sequence is None:
            if sequence_number != 0:
                raise SequenceIntegrityError(
                    f"first sequence number for this run was "
                    f"{sequence_number}, expected 0"
                )
            self._last_sequence = sequence_number
            self._last_payload = payload
            self.first_sequence = sequence_number
            return True

        expected = self._last_sequence + 1
        if sequence_number == expected:
            self._last_sequence = sequence_number
            self._last_payload = payload
            return True
        if sequence_number == self._last_sequence:
            if payload == self._last_payload:
                return False
            raise SequenceIntegrityError(
                f"conflicting redelivery at sequence {sequence_number}: "
                "payload differs from the previously accepted message at "
                "this sequence"
            )
        if sequence_number > expected:
            raise SequenceIntegrityError(
                f"sequence gap: expected {expected}, got {sequence_number}"
            )
        raise SequenceIntegrityError(
            f"out-of-order/late sequence: expected {expected} (or exact "
            f"redelivery of {self._last_sequence}), got {sequence_number}"
        )

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def accepted_count(self) -> int:
        """Distinct sequence numbers accepted. Acceptance requires 0..N-1
        without gaps, so this is ``last_sequence + 1``."""
        return 0 if self._last_sequence is None else self._last_sequence + 1


_monotonic = time.monotonic


class _IdleGuard:
    """Abort guard of a lifecycle-governed capture. Three notions of progress
    are kept apart so a healthy scan is never mistaken for a dead run and a
    dead run is never hidden behind unrelated traffic:

    - consumer progress -- any record taken from Kafka, of any run. Proves the
      consumer is advancing, not that the target run is alive.
    - target-run progress -- any record of the target run (RUN_START,
      telemetry, a skipped redelivery).
    - run inactivity -- time since the last target-run record once the run
      has started.

    While ARMED (scanning history for RUN_START, or waiting for a bridge that
    has not started yet) the inactivity clock is consumer progress. Once
    CAPTURING it is target-run progress only, so records of other runs never
    keep a run alive whose RUN_END is not coming.

    The clock starts at construction: the timeout must cover any wait for the
    bridge as well as the scan to RUN_START.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds
        self._last_consumed = _monotonic()
        self._last_target = self._last_consumed

    def consumed(self) -> None:
        self._last_consumed = _monotonic()

    def target_record(self) -> None:
        self._last_target = self._last_consumed = _monotonic()

    def expired(self, phase: RunPhase) -> bool:
        last = self._last_target if phase is RunPhase.CAPTURING else self._last_consumed
        return _monotonic() - last >= self._timeout


async def run_capture(
    *,
    settings: StreamingSettings,
    robot_id: str,
    robot_run_id: str,
    output_root: Path,
    stop_condition: Callable[[int], bool | FinalizationReason] | None = None,
    idle_timeout_seconds: float | None = None,
    poll_timeout_seconds: float = 1.0,
    registry: ChannelRegistry = DEFAULT_REGISTRY,
    stop_on_run_end: bool = False,
    robot_platform: str | None = None,
    start: KafkaStartPosition | None = None,
) -> CaptureResult:
    """Consume from Kafka and durably capture one run's messages into a
    finalized MCAP bag, returning a ``CaptureResult`` once the recording is
    durably finalized (or an identical one already was).

    ``stop_condition(message_count)`` is polled before every Kafka poll
    and decides when enough has been captured -- this function has no
    built-in notion of "done" itself (e.g. it never treats a
    ``/mission/status`` payload as a stop signal); a caller wanting an
    idle-timeout or wall-clock-deadline policy closes over its own clock
    inside the callable it passes in.

    ``stop_on_run_end`` makes the capture lifecycle-governed
    (``lifecycle.py``): the run must be a complete RUN_START, telemetry,
    RUN_END session, and its explicit ``RUN_END`` control event is the only way
    to finalize. The capture ends when ``RUN_END`` has been consumed (after it
    passed its own sequence validation). The bridge publishes ``RUN_END``
    after its last telemetry record on the same partition, so everything the
    bridge forwarded precedes it. A lifecycle violation raises
    ``RunLifecycleError`` at the first offending record.

    In this mode ``stop_condition`` and ``idle_timeout_seconds`` are abort
    guards (``_IdleGuard`` defines what counts as progress). If either ends the
    capture before ``RUN_END``, ``RunStartNotObservedError`` (the run never
    started) or ``RunEndNotObservedError`` is raised and nothing is finalized,
    because a recording that merely stopped arriving cannot be
    told from a complete one once it is a registered RobotRun. The partial bag
    stays for the next attempt to discard and rebuild from Kafka. Without
    ``stop_on_run_end`` the ``stop_condition`` alone ends and finalizes the
    capture (count-bounded synthetic runs in tests and benchmarks), and
    ``idle_timeout_seconds`` is not accepted.

    Capture never joins a consumer group and records no progress in Kafka: where
    an attempt starts reading is decided by its caller, and whether a run was
    captured is decided by its finalized bag. Without ``start`` it ``assign()``s
    every partition of the topic at its beginning
    (``KafkaTelemetryConsumer(start_from_beginning=True)``, a scan of the whole
    history). With ``start`` it ``assign()``s that one partition at that offset
    (``KafkaStartPosition``, normally the offset of the run's RUN_START): no
    record of any other partition is read. The position is validated (``StartPositionError`` if the
    partition or offset is not in the retained log) and never replaced by
    another: in a lifecycle-governed capture, RUN_START must be found between
    ``start.offset`` and the log end seen at startup, else
    ``RunStartNotObservedError``; a position after RUN_START fails the lifecycle
    at the run's first telemetry record. A re-run of an already finalized run
    converges onto the existing bag (or fails on different content) instead of
    resuming after its RUN_END.

    The attempt owns ``robot_run_id`` on ``output_root`` exclusively
    (``ownership.own_run``): a second concurrent attempt raises
    ``RunAlreadyOwnedError`` before touching any file.

    A stop condition may return the ``FinalizationReason`` it stands for
    (recorded in the capture receipt); a plain ``True`` records ``MANUAL``.

    Before the atomic finalize, a capture receipt (``receipt.py``) is written
    into the partial bag directory, so the recording and the acquisition
    metadata needed to publish it become durable as one unit.
    ``robot_platform`` is the source assertion carried into the receipt (and
    from there into the RobotRunManifest); it is not interpreted here.

    Each written message's ``log_time`` is the wall-clock instant this
    function took the record from Kafka (see ``mcap_writer.py``).
    """
    if idle_timeout_seconds is not None and not stop_on_run_end:
        raise ValueError("idle_timeout_seconds requires stop_on_run_end")
    if stop_condition is None and not stop_on_run_end:
        raise ValueError("a capture without stop_on_run_end needs a stop_condition")
    if start is not None and start.topic != settings.telemetry_topic:
        raise ValueError(
            f"start position topic {start.topic!r} is not the configured "
            f"telemetry topic {settings.telemetry_topic!r}"
        )

    with own_run(output_root, robot_run_id):
        # Before any file is touched: an unusable start position leaves an
        # existing partial bag alone.
        consumer = KafkaTelemetryConsumer(
            settings=settings,
            start_from_beginning=start is None,
            start_position=start,
        )
        try:
            return await _capture_with_consumer(
                consumer,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                output_root=output_root,
                stop_condition=stop_condition,
                idle_timeout_seconds=idle_timeout_seconds,
                poll_timeout_seconds=poll_timeout_seconds,
                registry=registry,
                stop_on_run_end=stop_on_run_end,
                robot_platform=robot_platform,
                start=start,
            )
        finally:
            await consumer.close()


async def _capture_with_consumer(
    consumer,
    *,
    robot_id: str,
    robot_run_id: str,
    output_root: Path,
    stop_condition: Callable[[int], bool | FinalizationReason] | None,
    idle_timeout_seconds: float | None,
    poll_timeout_seconds: float,
    registry: ChannelRegistry,
    stop_on_run_end: bool,
    robot_platform: str | None,
    start: KafkaStartPosition | None,
) -> CaptureResult:
    partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
    writer = McapCaptureWriter(bag_uri=str(partial_dir), registry=registry)
    run_ended = False
    lifecycle = RunLifecycle() if stop_on_run_end else None
    idle_guard = (
        _IdleGuard(idle_timeout_seconds) if idle_timeout_seconds is not None else None
    )
    run_filter = _RunFilter(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        partition=start.partition if start is not None else None,
    )
    tracker = _SequenceTracker()
    # Lifecycle control events (Phase 7.2.1) get their OWN independent
    # 0..N-1 validation, never merged with telemetry's -- they are
    # published on a separate sequence counter (the streaming bridge),
    # so conflating the two spaces would false-positive on the very
    # first control event (RUN_START always lands at control-sequence 0,
    # exactly where the first telemetry message also needs to land in
    # ITS OWN space).
    control_tracker = _SequenceTracker()

    first_offset: int | None = None
    last_offset: int | None = None
    source_topics: set[str] = set()
    stop_reason = FinalizationReason.MANUAL

    # With a start position, RUN_START exists below the log end seen at startup
    # (the position came from its delivery): a lifecycle-governed capture still
    # ARMED after that record has been consumed was pointed at the wrong place.
    start_window_end = consumer.end_offset if start is not None else None
    last_polled_offset: int | None = None

    try:
        while not run_ended:
            if (
                start_window_end is not None
                and lifecycle is not None
                and lifecycle.phase is RunPhase.ARMED
                and last_polled_offset is not None
                and last_polled_offset + 1 >= start_window_end
            ):
                raise RunStartNotObservedError(
                    f"RUN_START of robot_run_id={robot_run_id!r} not found in "
                    f"{start.topic}[{start.partition}] offsets "
                    f"{start.offset}..{start_window_end - 1}: wrong partition, "
                    "or a start offset after RUN_START"
                )
            stop = (
                stop_condition(writer.stats.message_count)
                if stop_condition is not None
                else False
            )
            if (
                not stop
                and idle_guard is not None
                and idle_guard.expired(lifecycle.phase)
            ):
                stop = FinalizationReason.IDLE_TIMEOUT
            if stop:
                if isinstance(stop, FinalizationReason):
                    stop_reason = stop
                break
            consumed = await consumer.poll(poll_timeout_seconds)
            receive_time_ns = time.time_ns()
            if consumed is None:
                continue
            last_polled_offset = consumed.offset
            if idle_guard is not None:
                idle_guard.consumed()
            if not run_filter.matches(consumed):
                continue
            if idle_guard is not None:
                idle_guard.target_record()
            source_topics.add(consumed.topic)

            envelope = consumed.envelope
            if is_control_envelope(envelope):
                # Validated (gap/duplicate/conflict) exactly as
                # strictly as telemetry, in its own independent
                # sequence space -- never silently bypassed -- but
                # never written to the MCAP: a control event is not
                # sensor data, and the channel registry
                # deliberately never includes it (so a genuinely
                # unsupported/unknown channel still fails loudly,
                # unambiguously, via UnsupportedChannelError).
                is_new_control = control_tracker.accept(
                    sequence_number=envelope.sequence_number,
                    payload=envelope.payload,
                )
                if lifecycle is not None:
                    lifecycle.on_control(
                        parse_run_event(envelope), redelivery=not is_new_control
                    )
                    run_ended = lifecycle.phase is RunPhase.ENDED
                    if run_ended:
                        # Before anything is validated or finalized: the
                        # producer's declared closing state against what
                        # this capture observed.
                        try:
                            declared = parse_run_end_telemetry_count(envelope)
                        except RunEndPayloadError as exc:
                            raise RunLifecycleError(str(exc)) from exc
                        check_run_complete(
                            declared=declared, observed=tracker.accepted_count
                        )
                if first_offset is None:
                    first_offset = consumed.offset
                last_offset = consumed.offset
                continue

            if lifecycle is not None:
                lifecycle.on_telemetry()
            should_write = tracker.accept(
                sequence_number=envelope.sequence_number,
                payload=envelope.payload,
            )
            if not should_write:
                continue

            writer.write_envelope(envelope, receive_time_ns=receive_time_ns)
            if first_offset is None:
                first_offset = consumed.offset
            last_offset = consumed.offset
    finally:
        # Always fsync/close the writer, whatever happened above --
        # but only proceed to validate/finalize on the clean
        # (non-exception) path below.
        writer.close()

    if stop_on_run_end and not run_ended:
        if lifecycle.phase is RunPhase.ARMED:
            raise RunStartNotObservedError(
                f"capture of robot_run_id={robot_run_id!r} stopped before "
                "its RUN_START; nothing recorded, not finalized"
            )
        raise RunEndNotObservedError(
            f"capture of robot_run_id={robot_run_id!r} stopped before its "
            f"RUN_END after {writer.stats.message_count} message(s); "
            "not finalized"
        )

    mcap_path = writer.mcap_file_path()
    validate_mcap_file(mcap_path, expected_message_count=writer.stats.message_count)

    # Everything the receipt claims about the bytes is read from the
    # validated partial file, not from the writer's counters.
    digest_hex = _sha256_file(Path(mcap_path))
    receipt = CaptureReceipt(
        run_id=robot_run_id,
        robot_id=robot_id,
        robot_platform=robot_platform,
        recording=ReceiptRecording(
            file=Path(mcap_path).name,
            format=RecordingFormat.MCAP,
            checksum=f"sha256:{digest_hex}",
            size_bytes=os.path.getsize(mcap_path),
        ),
        capture=CaptureInfo(
            source=CaptureSource(
                kind=CaptureSourceKind.KAFKA, topics=sorted(source_topics)
            ),
            source_clock=MCAP_LOG_TIME_CLOCK,
        ),
        message_count=writer.stats.message_count,
        per_channel_counts=dict(writer.stats.per_channel_counts),
        finalization=ReceiptFinalization(
            reason=(FinalizationReason.EXPLICIT_RUN_END if run_ended else stop_reason),
            finalized_at=datetime.now(UTC),
        ),
        kafka=ReceiptKafka(
            partition=run_filter.partition,
            first_offset=first_offset,
            last_offset=last_offset,
            first_sequence=tracker.first_sequence,
            last_sequence=tracker.last_sequence,
        ),
    )
    write_capture_receipt(partial_dir, receipt)

    try:
        final_dir = finalize_bag(output_root, robot_run_id)
        final_mcap_path = final_dir / Path(mcap_path).name
    except FinalBagExistsError:
        # A prior attempt for this robot_run_id already finalized (it
        # crashed before reporting, or the run is simply being captured
        # again), and this attempt independently consumed, wrote, and
        # validated the SAME messages again. That is convergence, not
        # a conflict: if the recorded messages genuinely match, report
        # the existing final file -- never silently overwrite it, and
        # never loop forever crashing on the same already-finalized file
        # either.
        #
        # "Match" ignores log_time only: each attempt stamps its own
        # receive time, so the bytes of two attempts legitimately
        # differ there and nowhere else. The existing file (and its
        # receive times) is the recording of record.
        existing_final_dir = final_bag_path(output_root, robot_run_id)
        existing_mcap_path = existing_final_dir / Path(mcap_path).name
        if not existing_mcap_path.is_file() or not same_recorded_content(
            str(existing_mcap_path), mcap_path
        ):
            # A genuine conflict (different content under the same
            # robot_run_id) -- never silently resolved, re-raise.
            raise
        final_dir = existing_final_dir
        final_mcap_path = existing_mcap_path
        digest_hex = _sha256_file(existing_mcap_path)
        # The existing bag keeps its own receipt (recording of record).
        # It must still describe the bytes beside it and the run this
        # attempt captured; a bag without a receipt predates receipts
        # and is left as it is -- a receipt is written only atomically
        # with finalize, never added afterwards.
        existing_receipt = read_capture_receipt(existing_final_dir)
        if existing_receipt is not None:
            if existing_receipt.recording.checksum != f"sha256:{digest_hex}":
                raise CaptureReceiptConflictError(
                    f"capture receipt of {existing_final_dir} does not match "
                    f"the checksum of {existing_mcap_path}"
                )
            check_existing_receipt_converges(existing_receipt, receipt)
        shutil.rmtree(Path(mcap_path).parent, ignore_errors=True)

    result = CaptureResult(
        robot_id=robot_id,
        robot_run_id=robot_run_id,
        path=final_mcap_path,
        message_count=writer.stats.message_count,
        partition=run_filter.partition,
        first_offset=first_offset,
        last_offset=last_offset,
        first_sequence=tracker.first_sequence,
        last_sequence=tracker.last_sequence,
        sha256=digest_hex,
        per_channel_counts=dict(writer.stats.per_channel_counts),
        receipt_path=(
            final_dir / CAPTURE_RECEIPT_FILENAME
            if (final_dir / CAPTURE_RECEIPT_FILENAME).is_file()
            else None
        ),
    )

    return result
