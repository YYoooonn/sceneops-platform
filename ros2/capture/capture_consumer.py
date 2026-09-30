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
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.streaming import ConsumedTelemetryEnvelope
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
from validation import validate_mcap_file


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
    stop_condition: Callable[[int], bool],
    poll_timeout_seconds: float = 1.0,
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
    """
    partial_dir = prepare_partial_bag_dir(output_root, robot_run_id)
    writer = McapCaptureWriter(bag_uri=str(partial_dir))
    run_filter = _RunFilter(robot_id=robot_id, robot_run_id=robot_run_id)
    tracker = _SequenceTracker()

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

    try:
        try:
            while not stop_condition(writer.stats.message_count):
                consumed = await consumer.poll(poll_timeout_seconds)
                if consumed is None:
                    continue
                if not run_filter.matches(consumed):
                    continue

                envelope = consumed.envelope
                should_write = tracker.accept(
                    sequence_number=envelope.sequence_number,
                    payload=envelope.payload,
                )
                if not should_write:
                    continue

                writer.write_envelope(envelope)
                if first_offset is None:
                    first_offset = consumed.offset
                last_offset = consumed.offset
        finally:
            # Always fsync/close the writer, whatever happened above --
            # but only proceed to validate/finalize/commit on the clean
            # (non-exception) path below.
            writer.close()

        mcap_path = writer.mcap_file_path()
        validate_mcap_file(
            mcap_path, expected_message_count=writer.stats.message_count
        )

        try:
            final_dir = finalize_bag(output_root, robot_run_id)
            final_mcap_path = final_dir / Path(mcap_path).name
            digest_hex = _sha256_file(final_mcap_path)
        except FinalBagExistsError:
            # A prior attempt for this robot_run_id already reached
            # finalize successfully but was killed/crashed before
            # committing Kafka offsets (the exact "after finalize, before
            # commit" crash boundary) -- Kafka never advanced past those
            # records, so this attempt independently consumed, wrote, and
            # validated the SAME messages again. That is convergence, not
            # a conflict: if the bytes genuinely match, commit the offset
            # now (the durability boundary this run reached is identical
            # to the prior one) and report the existing final file --
            # never silently overwrite it, and never loop forever
            # crashing on the same already-finalized file either.
            existing_final_dir = final_bag_path(output_root, robot_run_id)
            existing_mcap_path = existing_final_dir / Path(mcap_path).name
            new_digest = _sha256_file(mcap_path)
            if (
                not existing_mcap_path.is_file()
                or _sha256_file(existing_mcap_path) != new_digest
            ):
                # A genuine conflict (different content under the same
                # robot_run_id) -- never silently resolved, re-raise.
                raise
            final_dir = existing_final_dir
            final_mcap_path = existing_mcap_path
            digest_hex = new_digest
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
        )

        # Only now -- after the MCAP has been validated and durably,
        # atomically finalized on disk -- is it safe to acknowledge
        # these Kafka records as consumed. Never before.
        await consumer.commit()
        return result
    finally:
        await consumer.close()
