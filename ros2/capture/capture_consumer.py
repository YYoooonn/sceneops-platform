"""Orchestrates one run-scoped durable MCAP capture: Kafka -> RunFilter ->
SequenceTracker -> McapCaptureWriter -> validate -> finalize -> commit.

Frozen commit-boundary ordering (never reversed): consume -> write to the
temp/partial MCAP -> close the writer (fsync) -> validate by reading the
file back -> atomically finalize (rename + fsync parent dir) -> only then
commit Kafka offsets. If anything before the Kafka commit fails, this
function raises without committing, leaving the partial bag in place for
the next attempt to discard and rebuild from Kafka (see ``finalize.py``).

Lifecycle is externally controlled: this module has no notion of what a
"mission" is and never inspects the content of ``/mission/status`` (or
any other channel) to decide when to start or stop capturing -- the
caller decides, via ``stop_condition``.

**Lifecycle control envelopes (Phase 7.2.1).** ``RUN_START``/``RUN_END``
(``sceneops_core.streaming.control``) share this run's Kafka key/
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

from sceneops_core.robots.capture_receipt import (
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

from finalize import (
    FinalBagExistsError,
    final_bag_path,
    finalize_bag,
    prepare_partial_bag_dir,
)
from group_id import derive_capture_group_id
from mcap_writer import McapCaptureWriter
from receipt import (
    CaptureReceiptConflictError,
    check_existing_receipt_converges,
    read_capture_receipt,
    write_capture_receipt,
)
from validation import same_recorded_content, validate_mcap_file


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# The capture consumer-group BASE (conceptually
# SCENEOPS_STREAMING_KAFKA_CAPTURE_CONSUMER_GROUP_ID -- frozen as a code
# constant, not an actual environment variable, since Phase 6.3; this
# follow-up does not change that). Never used as a literal Kafka
# group.id directly -- every capture derives its own run-scoped group
# from this base (see derive_capture_group_id, group_id.py), so a
# capture attempt never shares committed-offset state with any other
# consumer, generic or otherwise, NOR with an independent RobotRun's own
# capture (Phase 6.6.1).
CAPTURE_CONSUMER_GROUP_ID = "sceneops-mcap-capture"


class PartitionInvariantError(RuntimeError):
    """A robot_run_id's messages spanned more than one Kafka partition."""


class SequenceIntegrityError(RuntimeError):
    """A sequence gap, conflicting duplicate, or out-of-order redelivery
    was observed for this robot_run_id's stream."""


class RunEndNotObservedError(RuntimeError):
    """A capture that must end on its run's RUN_END was stopped before it
    arrived. What was consumed may be a truncated recording, so nothing is
    finalized and no offset is committed."""


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

    def __init__(self, *, robot_id: str, robot_run_id: str) -> None:
        self._robot_id = robot_id
        self._robot_run_id = robot_run_id
        self._partition: int | None = None

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


async def run_capture(
    *,
    settings: StreamingSettings,
    robot_id: str,
    robot_run_id: str,
    output_root: Path,
    stop_condition: Callable[[int], bool | FinalizationReason],
    poll_timeout_seconds: float = 1.0,
    registry: ChannelRegistry = DEFAULT_REGISTRY,
    stop_on_run_end: bool = False,
    robot_platform: str | None = None,
) -> CaptureResult:
    """Consume from Kafka and durably capture one run's messages into a
    finalized MCAP bag, returning a ``CaptureResult`` once Kafka offsets
    have been committed.

    ``stop_condition(message_count)`` is polled before every Kafka poll
    and decides when enough has been captured -- this function has no
    built-in notion of "done" itself (e.g. it never treats a
    ``/mission/status`` payload as a stop signal); a caller wanting an
    idle-timeout or wall-clock-deadline policy closes over its own clock
    inside the callable it passes in.

    ``stop_on_run_end`` makes the run's explicit ``RUN_END`` control event
    the only way to finalize: the capture ends when ``RUN_END`` has been
    consumed (after it passed its own sequence validation). The bridge
    publishes ``RUN_END`` after its last telemetry record on the same
    partition, so everything the bridge forwarded precedes it. In this mode
    ``stop_condition`` is an abort guard (e.g. an idle timeout): if it ends
    the capture before ``RUN_END``, ``RunEndNotObservedError`` is raised and
    nothing is finalized or committed, because a recording that merely
    stopped arriving cannot be told from a complete one once it is a
    registered RobotRun. The partial bag stays for the next attempt to
    discard and rebuild from Kafka. Without ``stop_on_run_end`` the
    ``stop_condition`` alone ends and finalizes the capture (count-bounded
    synthetic runs in tests and benchmarks).

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
    partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
    writer = McapCaptureWriter(bag_uri=str(partial_dir), registry=registry)
    run_ended = False
    run_filter = _RunFilter(robot_id=robot_id, robot_run_id=robot_run_id)
    tracker = _SequenceTracker()
    # Lifecycle control events (Phase 7.2.1) get their OWN independent
    # 0..N-1 validation, never merged with telemetry's -- they are
    # published on a separate sequence counter (streaming_bridge_node.py),
    # so conflating the two spaces would false-positive on the very
    # first control event (RUN_START always lands at control-sequence 0,
    # exactly where the first telemetry message also needs to land in
    # ITS OWN space).
    control_tracker = _SequenceTracker()

    # Run-scoped, not the literal base -- see group_id.py's own docstring
    # for why (Phase 6.6.1): a group shared across every RobotRun let one
    # run's capture silently advance another's committed offset.
    group_id = derive_capture_group_id(
        base=CAPTURE_CONSUMER_GROUP_ID, robot_run_id=robot_run_id
    )
    consumer = KafkaTelemetryConsumer(
        settings=settings,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )

    first_offset: int | None = None
    last_offset: int | None = None
    source_topics: set[str] = set()
    stop_reason = FinalizationReason.MANUAL

    try:
        try:
            while not run_ended:
                stop = stop_condition(writer.stats.message_count)
                if stop:
                    if isinstance(stop, FinalizationReason):
                        stop_reason = stop
                    break
                consumed = await consumer.poll(poll_timeout_seconds)
                receive_time_ns = time.time_ns()
                if consumed is None:
                    continue
                if not run_filter.matches(consumed):
                    continue
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
                    control_tracker.accept(
                        sequence_number=envelope.sequence_number,
                        payload=envelope.payload,
                    )
                    if first_offset is None:
                        first_offset = consumed.offset
                    last_offset = consumed.offset
                    if (
                        stop_on_run_end
                        and parse_run_event(envelope) is RunEventType.RUN_END
                    ):
                        run_ended = True
                    continue

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
            # but only proceed to validate/finalize/commit on the clean
            # (non-exception) path below.
            writer.close()

        if stop_on_run_end and not run_ended:
            raise RunEndNotObservedError(
                f"capture of robot_run_id={robot_run_id!r} stopped before its "
                f"RUN_END after {writer.stats.message_count} message(s); "
                "not finalized"
            )

        mcap_path = writer.mcap_file_path()
        validate_mcap_file(
            mcap_path, expected_message_count=writer.stats.message_count
        )

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
                reason=(
                    FinalizationReason.EXPLICIT_RUN_END if run_ended else stop_reason
                ),
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
            # A prior attempt for this robot_run_id already reached
            # finalize successfully but was killed/crashed before
            # committing Kafka offsets (the exact "after finalize, before
            # commit" crash boundary) -- Kafka never advanced past those
            # records, so this attempt independently consumed, wrote, and
            # validated the SAME messages again. That is convergence, not
            # a conflict: if the recorded messages genuinely match, commit
            # the offset now (the durability boundary this run reached is
            # identical to the prior one) and report the existing final
            # file -- never silently overwrite it, and never loop forever
            # crashing on the same already-finalized file either.
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

        # Only now -- after the MCAP has been validated and durably,
        # atomically finalized on disk -- is it safe to acknowledge
        # these Kafka records as consumed. Never before.
        await consumer.commit()
        return result
    finally:
        await consumer.close()
