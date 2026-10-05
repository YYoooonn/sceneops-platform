"""Read-only scan of a capture output root (ADR-008 §3.1, §5.1, §8 step 12.3).

The Publisher is the one platform-side component that already mounts the
capture volume (read-only), so the capture-layout knowledge lives here and
nowhere else outside Capture itself. The scan classifies each ``run_id`` it
finds::

    <root>/.partial/<run_id>/   -> capture_unfinished (when no finalized bag)
    <root>/<run_id>/            -> finalized_with_receipt
                                   finalized_no_receipt
                                   finalized_receipt_invalid

and returns a :class:`~sceneops_core.robots.capture_scan.CaptureScanReport`,
the only thing the API reconciler ever sees of the capture volume. Nothing is
written, nothing is read beyond directory entries, file sizes and the (small)
receipt file: the recording bytes are never read, so the receipt's checksum is
not re-derived here -- publication re-derives every fact from the bytes
(ADR-008 L-7).

Database-free, like the rest of the Publisher.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sceneops_core.robots.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    CaptureReceiptError,
    load_canonical_capture_receipt,
)
from sceneops_core.robots.capture_scan import (
    CaptureClass,
    CaptureObservation,
    CaptureReceiptFacts,
    CaptureScanReport,
)
from sceneops_core.robots.manifest import RUN_ID_MAX_LENGTH, validate_identifier

from .facts import RecordingValidationError

# Capture's write target for bags that are not finalized yet (ros2/capture
# finalize.py owns this name; the scan only reads it).
PARTIAL_DIRNAME = ".partial"

_ERROR_MESSAGE_LIMIT = 300


def _is_run_id(name: str) -> bool:
    try:
        validate_identifier(name, field="run_id", max_length=RUN_ID_MAX_LENGTH)
    except ValueError:
        return False
    return True


def _error_text(error: Exception) -> str:
    message = " ".join(str(error).split())
    if len(message) > _ERROR_MESSAGE_LIMIT:
        message = message[: _ERROR_MESSAGE_LIMIT - 3] + "..."
    return f"{type(error).__name__}: {message}"


def _newest_mtime(directory: Path) -> datetime | None:
    try:
        stamps = [directory.stat().st_mtime]
        stamps.extend(child.stat().st_mtime for child in directory.iterdir())
    except FileNotFoundError:
        return None  # removed while scanning
    return datetime.fromtimestamp(max(stamps), tz=UTC)


def _receipt_facts(receipt: CaptureReceipt) -> CaptureReceiptFacts:
    return CaptureReceiptFacts(
        robot_id=receipt.robot_id,
        robot_platform=receipt.robot_platform,
        recording_file=receipt.recording.file,
        recording_checksum=receipt.recording.checksum,
        recording_size_bytes=receipt.recording.size_bytes,
        message_count=receipt.message_count,
        finalization_reason=receipt.finalization.reason.value,
        finalized_at=receipt.finalization.finalized_at,
    )


def _observe_finalized(
    run_id: str, final_dir: Path, *, partial_present: bool
) -> CaptureObservation:
    mcap_files = tuple(
        sorted(
            child.name
            for child in final_dir.iterdir()
            if child.suffix == ".mcap" and child.is_file()
        )
    )
    receipt_path = final_dir / CAPTURE_RECEIPT_FILENAME

    classification = CaptureClass.FINALIZED_NO_RECEIPT
    facts: CaptureReceiptFacts | None = None
    problem: str | None = None

    if receipt_path.is_file():
        try:
            receipt = load_canonical_capture_receipt(receipt_path.read_bytes())
        except CaptureReceiptError as exc:
            receipt = None
            problem = _error_text(exc)
        else:
            if receipt.run_id != run_id:
                problem = f"receipt_run_id_mismatch: {receipt.run_id!r}"
            elif receipt.recording.file not in mcap_files:
                problem = f"recording_file_missing: {receipt.recording.file}"
            elif (
                final_dir / receipt.recording.file
            ).stat().st_size != receipt.recording.size_bytes:
                problem = "recording_size_mismatch"
        if receipt is not None and problem is None:
            classification = CaptureClass.FINALIZED_WITH_RECEIPT
            facts = _receipt_facts(receipt)
        else:
            classification = CaptureClass.FINALIZED_RECEIPT_INVALID

    return CaptureObservation(
        run_id=run_id,
        classification=classification,
        partial_present=partial_present,
        final_present=True,
        mcap_files=mcap_files,
        receipt=facts,
        receipt_problem=problem,
        modified_at=_newest_mtime(final_dir),
    )


def scan_capture_volume(capture_root: Path) -> CaptureScanReport:
    """Classify every run found under a capture output root. The first
    finalized bag is the recording of record (ADR-008 L-6): a ``.partial``
    directory next to a finalized bag is evidence of a later attempt, not an
    unfinished capture."""
    if not capture_root.is_dir():
        raise RecordingValidationError(f"capture root not found: {capture_root}")

    final_dirs: dict[str, Path] = {}
    unrecognized: list[str] = []
    for entry in sorted(capture_root.iterdir()):
        if entry.name == PARTIAL_DIRNAME:
            continue
        if entry.is_dir() and _is_run_id(entry.name):
            final_dirs[entry.name] = entry
        else:
            unrecognized.append(entry.name)

    partial_dirs: dict[str, Path] = {}
    partial_root = capture_root / PARTIAL_DIRNAME
    if partial_root.is_dir():
        for entry in sorted(partial_root.iterdir()):
            if entry.is_dir() and _is_run_id(entry.name):
                partial_dirs[entry.name] = entry
            else:
                unrecognized.append(f"{PARTIAL_DIRNAME}/{entry.name}")

    runs: list[CaptureObservation] = []
    for run_id in sorted(set(final_dirs) | set(partial_dirs)):
        if run_id in final_dirs:
            runs.append(
                _observe_finalized(
                    run_id, final_dirs[run_id], partial_present=run_id in partial_dirs
                )
            )
        else:
            runs.append(
                CaptureObservation(
                    run_id=run_id,
                    classification=CaptureClass.CAPTURE_UNFINISHED,
                    partial_present=True,
                    final_present=False,
                    mcap_files=(),
                    receipt=None,
                    receipt_problem=None,
                    modified_at=_newest_mtime(partial_dirs[run_id]),
                )
            )

    return CaptureScanReport(
        runs=tuple(runs), unrecognized_entries=tuple(sorted(unrecognized))
    )


__all__ = ["PARTIAL_DIRNAME", "scan_capture_volume"]
