"""Phase 6.6 reliability matrix: TelemetryEnvelope.sequence_number
duplicate/gap/out-of-order behavior, exercised directly against
_SequenceTracker (capture_consumer.py) -- the exact same object
run_capture() uses, no fakes/mocks needed for this matrix since
_SequenceTracker has no I/O.

One test per matrix row (docs: capture-reliability-scale-baseline.md).
Frozen v1 policy -- these tests prove the CURRENT rejection/acceptance
behavior, they do not broaden it. A row intentionally rejected today
must keep raising SequenceIntegrityError; this file is the evidence for
that claim, not a change to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from capture_consumer import SequenceIntegrityError, _SequenceTracker  # noqa: E402


def _feed(tracker: _SequenceTracker, *pairs: tuple[int, bytes]) -> list[bool]:
    """Feed (sequence_number, payload) pairs in order, collecting each
    accept() call's return value. Stops (and lets the exception
    propagate) the moment accept() raises."""
    return [tracker.accept(sequence_number=seq, payload=payload) for seq, payload in pairs]


def test_matrix_row_a_normal_in_order() -> None:
    """0,1,2,3 -- every message accepted and written, in order."""
    tracker = _SequenceTracker()
    results = _feed(
        tracker, (0, b"p0"), (1, b"p1"), (2, b"p2"), (3, b"p3")
    )
    assert results == [True, True, True, True]
    assert tracker.last_sequence == 3


def test_matrix_row_b_exact_immediate_duplicate() -> None:
    """0,1,1,2 -- the immediate redelivery of "1" (same payload) is
    silently skipped (not written twice, not an error); 2 still accepted
    normally afterward."""
    tracker = _SequenceTracker()
    results = _feed(
        tracker, (0, b"p0"), (1, b"p1"), (1, b"p1"), (2, b"p2")
    )
    assert results == [True, True, False, True]
    assert tracker.last_sequence == 2


def test_matrix_row_c_conflicting_duplicate_same_sequence_different_payload() -> None:
    """0,1 then a second "1" with DIFFERENT payload -- rejected, never
    silently accepted as if it were a legitimate redelivery."""
    tracker = _SequenceTracker()
    _feed(tracker, (0, b"p0"), (1, b"p1"))
    with pytest.raises(SequenceIntegrityError, match="conflicting redelivery"):
        tracker.accept(sequence_number=1, payload=b"DIFFERENT-PAYLOAD")


def test_matrix_row_d_gap() -> None:
    """0,1,3 -- skipping 2 is a gap, rejected outright (v1 has no
    unbounded reorder buffer to wait for the missing message)."""
    tracker = _SequenceTracker()
    _feed(tracker, (0, b"p0"), (1, b"p1"))
    with pytest.raises(SequenceIntegrityError, match="sequence gap"):
        tracker.accept(sequence_number=3, payload=b"p3")


def test_matrix_row_e_late_sequence() -> None:
    """0,2,1 -- the second message (2) is itself a gap (expected 1) and
    is rejected immediately; the tracker never gets to evaluate the
    third message (1) on its own merits. Documents that "late" arrivals
    following a gap are caught by the gap check first, not treated as a
    separate case."""
    tracker = _SequenceTracker()
    tracker.accept(sequence_number=0, payload=b"p0")
    with pytest.raises(SequenceIntegrityError, match="sequence gap"):
        tracker.accept(sequence_number=2, payload=b"p2")


def test_matrix_row_f_duplicate_after_later_records() -> None:
    """0,1,2,1 -- after already advancing to 2, a redelivery of the
    EARLIER sequence 1 is rejected as out-of-order/late, not silently
    treated as a no-op duplicate (only the single immediately-preceding
    sequence is ever treated as a safe-to-skip duplicate, v1's bounded
    policy -- see _SequenceTracker's own docstring)."""
    tracker = _SequenceTracker()
    _feed(tracker, (0, b"p0"), (1, b"p1"), (2, b"p2"))
    with pytest.raises(SequenceIntegrityError, match="out-of-order/late"):
        tracker.accept(sequence_number=1, payload=b"p1")


def test_matrix_row_g_non_zero_first_sequence() -> None:
    """5,6,7 -- a capture attempt whose very first observed message is
    not sequence 0 is rejected immediately; this is the same guarantee
    the real streaming capture depends on (a captured run starts at
    first_sequence == 0)."""
    tracker = _SequenceTracker()
    with pytest.raises(SequenceIntegrityError, match="expected 0"):
        tracker.accept(sequence_number=5, payload=b"p5")
