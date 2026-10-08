"""Capture receipt I/O (ADR-008 §4.2 A1).

The receipt (``sceneops_recording.capture_receipt``) is written into the
*partial* bag directory, so the single atomic ``os.replace`` in
``finalize.py`` publishes the recording and its receipt together:

    <output_root>/.partial/<run_id>/  <run_id>_0.mcap + capture_receipt.json
        write receipt -> fsync receipt -> fsync directory
        -> (finalize.py) os.replace -> fsync output_root
    <output_root>/<run_id>/           <run_id>_0.mcap + capture_receipt.json

No finalized bag written by ``run_capture`` can therefore exist without its
receipt, and a crash before the rename leaves only a ``.partial`` directory
that the next attempt discards.
"""

from __future__ import annotations

import os
from pathlib import Path

from sceneops_recording.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    load_canonical_capture_receipt,
)


class CaptureReceiptConflictError(RuntimeError):
    """A finalized bag's receipt disagrees with the bytes next to it, or with
    the identity/publication inputs of the capture now being finalized."""


def write_capture_receipt(bag_dir: Path, receipt: CaptureReceipt) -> Path:
    """Durably write the receipt into ``bag_dir`` (exclusive create: a
    receipt is written once and never replaced)."""
    path = bag_dir / CAPTURE_RECEIPT_FILENAME
    with open(path, "xb") as f:
        f.write(receipt.to_canonical_bytes())
        f.flush()
        os.fsync(f.fileno())
    dir_fd = os.open(bag_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path


def read_capture_receipt(bag_dir: Path) -> CaptureReceipt | None:
    """The bag's receipt, or ``None`` when it has none. A receipt that exists
    but is malformed raises ``CaptureReceiptError``: it is never treated as
    absent."""
    path = bag_dir / CAPTURE_RECEIPT_FILENAME
    if not path.is_file():
        return None
    return load_canonical_capture_receipt(path.read_bytes())


def check_existing_receipt_converges(
    existing: CaptureReceipt, candidate: CaptureReceipt
) -> None:
    """A retry that re-captured the same records of an already-finalized bag
    keeps that bag's receipt (the first finalized bag is the recording of
    record). It may only do so if the retry would have described the run
    identically: same run, robot, platform and capture source. Checksum, time
    and Kafka position legitimately differ between attempts (receive times)
    and are not compared."""
    differing = [
        name
        for name, existing_value, candidate_value in (
            ("run_id", existing.run_id, candidate.run_id),
            ("robot_id", existing.robot_id, candidate.robot_id),
            ("robot_platform", existing.robot_platform, candidate.robot_platform),
            ("capture", existing.capture, candidate.capture),
        )
        if existing_value != candidate_value
    ]
    if differing:
        raise CaptureReceiptConflictError(
            "the finalized bag's capture receipt conflicts with this capture "
            f"attempt in: {', '.join(differing)}; refusing to converge"
        )
