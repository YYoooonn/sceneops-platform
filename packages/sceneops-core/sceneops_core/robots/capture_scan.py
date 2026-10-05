"""Capture-volume observation report (ADR-008 §3.1, §5.1, §8 step 12.3).

Capture owns its volume and the platform never mounts it. What crosses the
boundary is this *report*: a read-only, DB-free scan of one capture output root
(``sceneops_integrations.recording.capture_scan``) produces a
:class:`CaptureScanReport`; the API reconciler accepts such a report and joins
it with the published and registered facts by ``run_id``. The report is
the only coupling between the capture layout and the platform: nothing outside
the scanner knows about ``.partial`` directories or bag file names.

A capture observation records facts about one ``run_id`` and says nothing
about publication or registration. It never overrides the MCAP bytes (ADR-008
L-7): the receipt-derived values here are the receipt's own claims.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

CAPTURE_SCAN_SCHEMA_V1: Final = "sceneops.capture_scan/v1"


class CaptureClass(StrEnum):
    # <root>/.partial/<run_id>/ exists and no finalized bag does: capture was
    # interrupted or is still running. Age is not judged here (see
    # ``modified_at``).
    CAPTURE_UNFINISHED = "capture_unfinished"
    # Finalized bag with a valid capture receipt consistent with the bag.
    FINALIZED_WITH_RECEIPT = "finalized_with_receipt"
    # Finalized bag without a receipt (legacy / batch): publishable only with
    # explicit publication inputs.
    FINALIZED_NO_RECEIPT = "finalized_no_receipt"
    # Finalized bag whose receipt is malformed, belongs to another run or
    # disagrees with the bag on file name or size. Never treated as absent.
    FINALIZED_RECEIPT_INVALID = "finalized_receipt_invalid"


class _CaptureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureReceiptFacts(_CaptureModel):
    """The receipt's claims that reconciliation reports or cross-checks."""

    robot_id: str
    robot_platform: str | None
    recording_file: str
    recording_checksum: str
    recording_size_bytes: int
    message_count: int
    finalization_reason: str
    finalized_at: datetime


class CaptureObservation(_CaptureModel):
    run_id: str
    classification: CaptureClass
    partial_present: bool
    final_present: bool
    # ``*.mcap`` files in the finalized bag, sorted. Empty without a bag.
    mcap_files: tuple[str, ...]
    # Receipt claims; None when the receipt is absent or unreadable.
    receipt: CaptureReceiptFacts | None
    # Why a receipt was rejected ("ErrorType: message" or a reason code);
    # set iff classification == FINALIZED_RECEIPT_INVALID.
    receipt_problem: str | None
    # Newest mtime of the finalized bag directory, else of the partial one.
    # The capture host's clock; indicative only across hosts.
    modified_at: datetime | None


class CaptureScanReport(_CaptureModel):
    schema_version: Literal["sceneops.capture_scan/v1"] = CAPTURE_SCAN_SCHEMA_V1
    runs: tuple[CaptureObservation, ...]
    # Entries under the capture root that are not run directories (names that
    # are not valid run identifiers, loose files). Reported, not interpreted.
    unrecognized_entries: tuple[str, ...]


__all__ = [
    "CAPTURE_SCAN_SCHEMA_V1",
    "CaptureClass",
    "CaptureObservation",
    "CaptureReceiptFacts",
    "CaptureScanReport",
]
