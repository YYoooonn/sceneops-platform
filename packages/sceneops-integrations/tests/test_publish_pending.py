"""``publish-pending``: one-shot recovery of publication from finalized captures
(ADR-008 §3.2, §5.1, Phase 12.4).

Capture directories are built the way Capture leaves them (see
``test_publish_from_capture.make_capture``) and published into a local
ArtifactStore. Every contract here is about what the command does to durable
state: it converges on retries, skips what is already complete, and never
overwrites or repairs a conflict.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sceneops_core.robots.capture_receipt import CAPTURE_RECEIPT_FILENAME
from sceneops_integrations.recording import (
    PendingOutcome,
    publish_from_capture,
    publish_pending,
)
from sceneops_storage import LocalArtifactStore

DEFAULT_MESSAGES = [
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_000_000_001_999),
    ("/vehicle/imu", "sensor_msgs/msg/Imu", 1_700_000_000_500_000_000),
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_001_000_000_000),
]
OTHER_MESSAGES = [
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_100_000_000_000),
    ("/vehicle/imu", "sensor_msgs/msg/Imu", 1_700_000_100_500_000_000),
]


class _SpyStore(LocalArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root_uri=str(root))
        self.writes: list[str] = []

    async def write_bytes(self, uri, data):
        self.writes.append(uri)
        await super().write_bytes(uri, data)


@pytest.fixture()
def store_root(tmp_path: Path) -> Path:
    path = tmp_path / "store"
    path.mkdir()
    return path


@pytest.fixture()
def capture_root(tmp_path: Path) -> Path:
    path = tmp_path / "capture"
    path.mkdir()
    return path


def _root_uri(store_root: Path) -> str:
    return str(store_root / "robot_runs")


async def _run(store_root: Path, capture_root: Path, store=None):
    return await publish_pending(
        artifact_store=store or _SpyStore(store_root),
        root_uri=_root_uri(store_root),
        capture_root=capture_root,
    )


def _by_run(report) -> dict:
    return {result.run_id: result for result in report.results}


async def test_publishes_every_finalized_capture_with_a_receipt(
    make_capture, store_root, capture_root
):
    make_capture(capture_root, run_id="run-a")
    make_capture(capture_root, run_id="run-b", messages=OTHER_MESSAGES)

    report = await _run(store_root, capture_root)

    results = _by_run(report)
    assert {r.outcome for r in results.values()} == {PendingOutcome.PUBLISHED}
    assert all(r.recording_written and r.manifest_written for r in results.values())
    assert report.counts == {"published": 2}
    assert not report.failed
    store = LocalArtifactStore(root_uri=str(store_root))
    for run_id, result in results.items():
        assert await store.exists(result.manifest_uri)
        assert result.manifest_uri.endswith(f"/{run_id}/robot_run_manifest.json")


async def test_a_second_pass_changes_nothing(make_capture, store_root, capture_root):
    make_capture(capture_root, run_id="run-a")
    first = await _run(store_root, capture_root)

    spy = _SpyStore(store_root)
    second = await _run(store_root, capture_root, store=spy)

    assert spy.writes == []  # converged: not one byte written
    (result,) = second.results
    assert result.outcome == PendingOutcome.SKIPPED
    assert result.reason == "already_published"
    assert result.manifest_checksum == first.results[0].manifest_checksum


async def test_converges_with_a_publication_made_by_publish_from_capture(
    make_capture, store_root, capture_root
):
    """One implementation path: a run published by ``publish --from-capture`` is
    recognised as complete, with the same manifest, and not rewritten."""
    directory = make_capture(capture_root, run_id="run-a")
    direct = await publish_from_capture(
        artifact_store=LocalArtifactStore(root_uri=str(store_root)),
        root_uri=_root_uri(store_root),
        capture_dir=directory,
    )

    spy = _SpyStore(store_root)
    report = await _run(store_root, capture_root, store=spy)

    (result,) = report.results
    assert result.reason == "already_published"
    assert result.manifest_checksum == direct.manifest_checksum
    assert spy.writes == []


async def test_resumes_a_publication_that_stopped_after_the_recording(
    make_capture, store_root, capture_root
):
    """Crash between P3 and P5: the recording is in the store, the manifest
    marker is not. The pass reuses the recording and writes only the manifest."""
    directory = make_capture(capture_root, run_id="run-a")
    store = LocalArtifactStore(root_uri=str(store_root))
    recording_uri = f"{_root_uri(store_root)}/run-a/recording.mcap"
    await store.write_bytes(recording_uri, (directory / "run-a_0.mcap").read_bytes())

    spy = _SpyStore(store_root)
    report = await _run(store_root, capture_root, store=spy)

    (result,) = report.results
    assert result.outcome == PendingOutcome.PUBLISHED
    assert result.recording_written is False and result.manifest_written is True
    assert spy.writes == [result.manifest_uri]


async def test_never_repairs_a_conflicting_recording(
    make_capture, store_root, capture_root
):
    """The store holds different bytes under the write-once recording key. The
    pass reports the failure, writes no manifest and does not overwrite."""
    make_capture(capture_root, run_id="run-a")
    store = LocalArtifactStore(root_uri=str(store_root))
    recording_uri = f"{_root_uri(store_root)}/run-a/recording.mcap"
    await store.write_bytes(recording_uri, b"someone else's bytes")

    spy = _SpyStore(store_root)
    report = await _run(store_root, capture_root, store=spy)

    (result,) = report.results
    assert result.outcome == PendingOutcome.FAILED
    assert result.reason == "publish_failed"
    assert "RecordingPublicationConflictError" in result.error
    assert report.failed
    assert await store.read_bytes(recording_uri) == b"someone else's bytes"
    assert not await store.exists(
        f"{_root_uri(store_root)}/run-a/robot_run_manifest.json"
    )
    assert spy.writes == []


async def test_a_conflicting_manifest_is_left_for_an_operator(
    make_capture, store_root, capture_root
):
    """A manifest exists but names a different recording than the object in the
    store: the pass classifies the contradiction and neither publishes nor
    overwrites anything."""
    directory = make_capture(capture_root, run_id="run-a")
    # Publish a *different* capture under the same run id, then swap the bag.
    await _run(store_root, capture_root)
    other = make_capture(
        capture_root.parent / "swap", run_id="run-a", messages=OTHER_MESSAGES
    )
    (directory / "run-a_0.mcap").write_bytes((other / "run-a_0.mcap").read_bytes())
    (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(
        (other / CAPTURE_RECEIPT_FILENAME).read_bytes()
    )
    store = LocalArtifactStore(root_uri=str(store_root))
    manifest_uri = f"{_root_uri(store_root)}/run-a/robot_run_manifest.json"
    before = await store.read_bytes(manifest_uri)

    spy = _SpyStore(store_root)
    report = await _run(store_root, capture_root, store=spy)

    (result,) = report.results
    # Complete publication of the original bytes exists: it is never rewritten
    # to match the swapped bag.
    assert result.outcome == PendingOutcome.SKIPPED
    assert result.reason == "already_published"
    assert spy.writes == []
    assert await store.read_bytes(manifest_uri) == before


async def test_a_manifest_without_its_recording_is_not_repaired(
    make_capture, store_root, capture_root
):
    make_capture(capture_root, run_id="run-a")
    await _run(store_root, capture_root)
    store = LocalArtifactStore(root_uri=str(store_root))
    os.remove(f"{_root_uri(store_root)}/run-a/recording.mcap")

    spy = _SpyStore(store_root)
    report = await _run(store_root, capture_root, store=spy)

    (result,) = report.results
    assert result.outcome == PendingOutcome.SKIPPED
    assert result.reason == "publication_manifest_without_recording"
    assert spy.writes == []
    assert not await store.exists(f"{_root_uri(store_root)}/run-a/recording.mcap")


async def test_legacy_and_unusable_and_unfinished_captures_are_skipped(
    make_capture, store_root, capture_root
):
    legacy = make_capture(capture_root, run_id="run-legacy")
    (legacy / CAPTURE_RECEIPT_FILENAME).unlink()
    broken = make_capture(capture_root, run_id="run-broken")
    (broken / CAPTURE_RECEIPT_FILENAME).write_bytes(b"{not a receipt")
    partial = capture_root / ".partial" / "run-partial"
    partial.mkdir(parents=True)
    (partial / "run-partial_0.mcap").write_bytes(b"half")

    spy = _SpyStore(store_root)
    report = await _run(store_root, capture_root, store=spy)

    reasons = {r.run_id: (r.outcome, r.reason) for r in report.results}
    assert reasons == {
        "run-broken": (PendingOutcome.SKIPPED, "receipt_invalid"),
        "run-legacy": (PendingOutcome.SKIPPED, "finalized_no_receipt"),
        "run-partial": (PendingOutcome.SKIPPED, "capture_unfinished"),
    }
    assert spy.writes == []


async def test_one_failing_capture_does_not_block_the_others(
    make_capture, store_root, capture_root
):
    make_capture(capture_root, run_id="run-a")
    tampered = make_capture(capture_root, run_id="run-b", messages=OTHER_MESSAGES)
    # Same size, different bytes: the scan cannot see it (it never reads the
    # recording); publication re-derives the checksum and fails loudly.
    bag = tampered / "run-b_0.mcap"
    bag.write_bytes(b"\x00" * bag.stat().st_size)
    make_capture(capture_root, run_id="run-c", messages=DEFAULT_MESSAGES[:2])

    report = await _run(store_root, capture_root)

    outcomes = {r.run_id: r.outcome for r in report.results}
    assert outcomes == {
        "run-a": PendingOutcome.PUBLISHED,
        "run-b": PendingOutcome.FAILED,
        "run-c": PendingOutcome.PUBLISHED,
    }
    assert report.counts == {"failed": 1, "published": 2}


async def test_a_missing_capture_root_is_an_error_not_an_empty_pass(
    store_root, tmp_path
):
    with pytest.raises(Exception, match="capture root not found"):
        await _run(store_root, tmp_path / "does-not-exist")


# ── the command ──────────────────────────────────────────────────────────────


def _cli(capture_root: Path, store_root: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "local",
        "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": str(store_root),
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sceneops_integrations.recording",
            "publish-pending",
            "--capture-root",
            str(capture_root),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_prints_the_report_and_converges_on_rerun(
    make_capture, store_root, capture_root
):
    make_capture(capture_root, run_id="run-a")

    first = _cli(capture_root, store_root)
    second = _cli(capture_root, store_root)

    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["counts"] == {"published": 1}
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["results"][0]["reason"] == "already_published"


def test_cli_exits_2_when_a_publish_failed_but_still_prints_the_report(
    make_capture, store_root, capture_root
):
    directory = make_capture(capture_root, run_id="run-a")
    mcap = directory / "run-a_0.mcap"
    mcap.write_bytes(b"\x00" * mcap.stat().st_size)  # same size: only the bytes lie

    result = _cli(capture_root, store_root)

    assert result.returncode == 2
    assert json.loads(result.stdout)["counts"] == {"failed": 1}


def test_cli_exits_1_when_the_capture_root_cannot_be_read(store_root, tmp_path):
    result = _cli(tmp_path / "missing", store_root)

    assert result.returncode == 1
    assert "publish-pending failed" in result.stderr
