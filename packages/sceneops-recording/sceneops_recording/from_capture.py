"""Recoverable publication of a finalized capture (ADR-008 §4.2 A1 rule 2).

``publish_from_capture`` reads the capture receipt that Capture wrote
atomically with the finalized bag and publishes the recording and its
RobotRunManifest through the same code path as explicit-input publication
(``publish_recording_bytes``). A finalized capture directory alone is thus
sufficient to publish after any restart: no argument has to be re-typed, and
retries cannot drift.

The receipt supplies only what the bytes cannot: ``robot_id``,
``robot_platform``, the capture source and the source clock. Every fact in
the manifest -- checksum, size, time range, channels -- is derived from the
MCAP bytes. The receipt's own claims about those bytes (checksum, size,
message count, per-channel counts) are checked against what the bytes say and
any disagreement fails publication *before* anything is written. The receipt
is publication input only: it is never uploaded and never overrides bytes.

Database-free, like the rest of the Publisher.
"""

from __future__ import annotations

import os
from pathlib import Path

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_recording.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    load_canonical_capture_receipt,
)

from .facts import RecordingFacts, RecordingValidationError, sha256_checksum
from .publisher import (
    RecordingPublication,
    publish_recording_bytes,
    read_finalized_recording,
)


class CaptureReceiptMissingError(RecordingValidationError):
    """The capture directory has no capture receipt; it can only be published
    with explicit publication inputs."""


class CaptureReceiptMismatchError(RecordingValidationError):
    """The capture receipt disagrees with the capture directory or with the
    bytes of the recording next to it."""


def read_capture_receipt(capture_dir: Path) -> CaptureReceipt:
    """The receipt of a finalized capture directory. A malformed receipt
    raises ``CaptureReceiptError`` (never treated as absent)."""
    if ".partial" in capture_dir.parts:
        raise RecordingValidationError(
            f"refusing to publish a .partial (not finalized) capture: {capture_dir}"
        )
    if not capture_dir.is_dir():
        raise RecordingValidationError(f"capture directory not found: {capture_dir}")
    path = capture_dir / CAPTURE_RECEIPT_FILENAME
    if not path.is_file():
        raise CaptureReceiptMissingError(
            f"{capture_dir} has no {CAPTURE_RECEIPT_FILENAME}; publish it with "
            "explicit publication inputs instead"
        )
    return load_canonical_capture_receipt(path.read_bytes())


def _verify_receipt_against_facts(
    receipt: CaptureReceipt, facts: RecordingFacts
) -> None:
    if facts.message_count != receipt.message_count:
        raise CaptureReceiptMismatchError(
            f"receipt message_count={receipt.message_count} but the recording "
            f"holds {facts.message_count} messages"
        )
    derived = {channel.topic: channel.message_count for channel in facts.channels}
    if derived != receipt.per_channel_counts:
        raise CaptureReceiptMismatchError(
            f"receipt per_channel_counts={receipt.per_channel_counts} but the "
            f"recording holds {derived}"
        )


async def publish_from_capture(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    capture_dir: Path,
) -> RecordingPublication:
    """Publish a finalized capture directory (``<root>/<run_id>/`` holding the
    MCAP and ``capture_receipt.json``). Idempotent: a retry reuses both
    objects and returns the same manifest bytes; conflicting existing content
    fails loudly."""
    receipt = read_capture_receipt(capture_dir)

    # The directory is the identity the receipt must belong to: a receipt
    # copied next to a different run's bytes must not publish under its name.
    directory_name = Path(os.path.abspath(capture_dir)).name
    if directory_name != receipt.run_id:
        raise CaptureReceiptMismatchError(
            f"capture directory {directory_name!r} does not match the receipt's "
            f"run_id {receipt.run_id!r}"
        )

    data = read_finalized_recording(capture_dir / receipt.recording.file)
    if len(data) != receipt.recording.size_bytes:
        raise CaptureReceiptMismatchError(
            f"receipt size_bytes={receipt.recording.size_bytes} but "
            f"{receipt.recording.file} has {len(data)} bytes"
        )
    if sha256_checksum(data) != receipt.recording.checksum:
        raise CaptureReceiptMismatchError(
            f"receipt checksum={receipt.recording.checksum} but "
            f"{receipt.recording.file} is {sha256_checksum(data)}"
        )

    return await publish_recording_bytes(
        artifact_store=artifact_store,
        root_uri=root_uri,
        recording_bytes=data,
        run_id=receipt.run_id,
        robot_id=receipt.robot_id,
        robot_platform=receipt.robot_platform,
        capture_source=receipt.capture.source,
        source_clock=receipt.capture.source_clock,
        verify_facts=lambda facts: _verify_receipt_against_facts(receipt, facts),
    )
