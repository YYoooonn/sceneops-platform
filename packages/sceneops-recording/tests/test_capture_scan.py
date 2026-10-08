"""Read-only scan of a capture output root (ADR-008 §5.1, Phase 12.3).

Directories are laid out exactly as Capture leaves them (``.partial/<run>/`` and
``<run>/`` holding an MCAP and ``capture_receipt.json``). The scan never reads
recording bytes, so the MCAP here is opaque and only its size matters.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sceneops_recording.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    FinalizationReason,
    ReceiptFinalization,
    ReceiptKafka,
    ReceiptRecording,
)
from sceneops_recording.capture_scan import CaptureClass
from sceneops_core.robots.manifest import (
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    RecordingFormat,
)
from sceneops_recording import (
    RecordingValidationError,
    scan_capture_volume,
)

_SOURCE = CaptureSource(kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.v1"])
_BYTES = b"opaque-mcap-bytes" * 8


def _receipt(run_id: str, data: bytes = _BYTES, **overrides) -> CaptureReceipt:
    fields = dict(
        run_id=run_id,
        robot_id="robot-001",
        robot_platform="replay",
        recording=ReceiptRecording(
            file=f"{run_id}_0.mcap",
            format=RecordingFormat.MCAP,
            checksum="sha256:" + hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        ),
        capture=CaptureInfo(source=_SOURCE, source_clock="mcap_log_time"),
        message_count=3,
        per_channel_counts={"/odom": 3},
        finalization=ReceiptFinalization(
            reason=FinalizationReason.EXPLICIT_RUN_END,
            finalized_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
        ),
        kafka=ReceiptKafka(
            partition=0,
            first_offset=0,
            last_offset=2,
            first_sequence=0,
            last_sequence=2,
        ),
    )
    fields.update(overrides)
    return CaptureReceipt(**fields)


def finalized(root: Path, run_id: str, *, receipt: bool | bytes = True) -> Path:
    directory = root / run_id
    directory.mkdir(parents=True)
    (directory / f"{run_id}_0.mcap").write_bytes(_BYTES)
    if receipt is True:
        (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(
            _receipt(run_id).to_canonical_bytes()
        )
    elif receipt is not False:
        (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(receipt)
    return directory


def partial(root: Path, run_id: str) -> Path:
    directory = root / ".partial" / run_id
    directory.mkdir(parents=True)
    (directory / f"{run_id}_0.mcap").write_bytes(b"half-writ")
    return directory


def _by_run(root: Path):
    return {run.run_id: run for run in scan_capture_volume(root).runs}


def test_every_capture_class_is_classified(tmp_path):
    finalized(tmp_path, "run-ok")
    finalized(tmp_path, "run-legacy", receipt=False)
    finalized(tmp_path, "run-garbled", receipt=b"{not a receipt")
    partial(tmp_path, "run-live")

    runs = _by_run(tmp_path)

    assert {k: v.classification for k, v in runs.items()} == {
        "run-garbled": CaptureClass.FINALIZED_RECEIPT_INVALID,
        "run-legacy": CaptureClass.FINALIZED_NO_RECEIPT,
        "run-live": CaptureClass.CAPTURE_UNFINISHED,
        "run-ok": CaptureClass.FINALIZED_WITH_RECEIPT,
    }
    ok = runs["run-ok"]
    assert ok.receipt.robot_id == "robot-001"
    assert ok.receipt.recording_size_bytes == len(_BYTES)
    assert ok.receipt.finalization_reason == "explicit_run_end"
    assert ok.mcap_files == ("run-ok_0.mcap",)
    assert ok.final_present and not ok.partial_present
    assert runs["run-live"].partial_present and not runs["run-live"].final_present
    assert runs["run-live"].mcap_files == ()
    assert "CaptureReceiptError" in runs["run-garbled"].receipt_problem
    assert runs["run-garbled"].receipt is None
    assert (
        runs["run-legacy"].receipt is None
        and runs["run-legacy"].receipt_problem is None
    )
    assert all(run.modified_at is not None for run in runs.values())


def test_a_partial_directory_beside_a_finalized_bag_is_not_an_unfinished_capture(
    tmp_path,
):
    # A later attempt left .partial behind; the finalized bag is the recording
    # of record (L-6).
    finalized(tmp_path, "run-1")
    partial(tmp_path, "run-1")

    run = _by_run(tmp_path)["run-1"]

    assert run.classification == CaptureClass.FINALIZED_WITH_RECEIPT
    assert run.partial_present and run.final_present


def test_receipt_of_another_run_is_invalid_not_ignored(tmp_path):
    directory = finalized(tmp_path, "run-a", receipt=False)
    (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(
        _receipt("run-b").to_canonical_bytes()
    )

    run = _by_run(tmp_path)["run-a"]

    assert run.classification == CaptureClass.FINALIZED_RECEIPT_INVALID
    assert run.receipt_problem.startswith("receipt_run_id_mismatch")


def test_receipt_naming_a_missing_or_resized_recording_is_invalid(tmp_path):
    missing = finalized(tmp_path, "run-missing")
    (missing / "run-missing_0.mcap").unlink()
    resized = finalized(tmp_path, "run-resized")
    (resized / "run-resized_0.mcap").write_bytes(_BYTES + b"more")

    runs = _by_run(tmp_path)

    assert runs["run-missing"].receipt_problem == (
        "recording_file_missing: run-missing_0.mcap"
    )
    assert runs["run-resized"].receipt_problem == "recording_size_mismatch"
    assert all(
        run.classification == CaptureClass.FINALIZED_RECEIPT_INVALID
        for run in runs.values()
    )


def test_non_canonical_receipt_is_invalid(tmp_path):
    finalized(tmp_path, "run-1", receipt=_receipt("run-1").to_canonical_bytes() + b"\n")

    run = _by_run(tmp_path)["run-1"]

    assert run.classification == CaptureClass.FINALIZED_RECEIPT_INVALID
    assert "NonCanonicalCaptureReceiptError" in run.receipt_problem


def test_unrecognized_entries_are_reported_not_scanned(tmp_path):
    finalized(tmp_path, "run-1")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "bad name").mkdir()
    (tmp_path / ".partial" / "stray.tmp").parent.mkdir(exist_ok=True)
    (tmp_path / ".partial" / "stray.tmp").write_text("x")

    report = scan_capture_volume(tmp_path)

    assert [run.run_id for run in report.runs] == ["run-1"]
    assert report.unrecognized_entries == (
        ".partial/stray.tmp",
        "bad name",
        "notes.txt",
    )


def test_missing_capture_root_fails_loudly(tmp_path):
    with pytest.raises(RecordingValidationError):
        scan_capture_volume(tmp_path / "nowhere")


def test_empty_root_scans_to_nothing(tmp_path):
    report = scan_capture_volume(tmp_path)

    assert report.runs == () and report.unrecognized_entries == ()


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def test_scan_is_read_only_and_deterministic(tmp_path):
    finalized(tmp_path, "run-b")
    finalized(tmp_path, "run-a", receipt=False)
    partial(tmp_path, "run-c")
    before = _snapshot(tmp_path)

    first = scan_capture_volume(tmp_path)
    second = scan_capture_volume(tmp_path)

    assert [run.run_id for run in first.runs] == ["run-a", "run-b", "run-c"]
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert _snapshot(tmp_path) == before


def test_scan_never_reads_recording_bytes(tmp_path, monkeypatch):
    directory = finalized(tmp_path, "run-1")
    mcap = directory / "run-1_0.mcap"
    real_read_bytes = Path.read_bytes

    def guarded(self):
        assert self != mcap, "the scan read recording bytes"
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)

    assert _by_run(tmp_path)["run-1"].classification == (
        CaptureClass.FINALIZED_WITH_RECEIPT
    )
