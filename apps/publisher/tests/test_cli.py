"""The Publisher as a process (``python -m sceneops_publisher``): exit codes, what is
written to stdout and stderr, and that a fresh process converges on the objects an
earlier one wrote. The publication logic itself is tested in
``packages/sceneops-recording/tests``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sceneops_core.robots.manifest import load_canonical_robot_run_manifest
from sceneops_recording.capture_scan import CaptureClass, CaptureScanReport
from sceneops_recording.testing.captures import (
    write_finalized_capture,
    write_partial_capture,
)


@pytest.fixture()
def make_capture():
    return write_finalized_capture


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


def _publisher_env(store_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "local",
        "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": str(store_root),
    }


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sceneops_publisher", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _cli(capture_root: Path, store_root: Path) -> subprocess.CompletedProcess:
    return _run(
        "publish-pending",
        "--capture-root",
        str(capture_root),
        env=_publisher_env(store_root),
    )


def _records(text: str, event: str) -> list[dict]:
    """The ``<event> {json}`` records of a log stream (a handler may prefix the
    line, so the event is looked for rather than assumed at column 0)."""
    marker = f"{event} {{"
    return [
        json.loads(line[line.index(marker) + len(event) + 1 :])
        for line in text.splitlines()
        if marker in line
    ]


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


def test_the_cli_logs_records_to_stderr_and_keeps_stdout_the_report(
    make_capture, store_root, capture_root
):
    make_capture(capture_root, run_id="run-a")

    result = _cli(capture_root, store_root)

    assert result.returncode == 0, result.stderr
    json.loads(result.stdout)  # nothing but the report
    (action,) = _records(result.stderr, "acquisition_recovery")
    assert action["run_id"] == "run-a" and action["outcome"] == "published"
    assert len(_records(result.stderr, "acquisition_recovery_pass")) == 1


def test_a_fresh_process_publishes_from_the_capture_directory_alone(
    store_root, make_capture, tmp_path
) -> None:
    capture_dir = make_capture(tmp_path / "capture", run_id="run-001")
    command = ["publish", "--from-capture", str(capture_dir)]
    env = _publisher_env(store_root)

    first = _run(*command, env=env)
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)
    assert first_result["recording_written"] and first_result["manifest_written"]

    # A second, independent process: converges to the same objects.
    second = _run(*command, env=env)
    assert second.returncode == 0, second.stderr
    second_result = json.loads(second.stdout)
    assert not second_result["recording_written"]
    assert not second_result["manifest_written"]
    assert second_result["manifest_checksum"] == first_result["manifest_checksum"]
    manifest = load_canonical_robot_run_manifest(
        Path(second_result["manifest_uri"]).read_bytes()
    )
    assert manifest.robot_id == "robot-001"


def test_cli_scan_capture_prints_a_report_the_api_can_load(tmp_path):
    write_finalized_capture(tmp_path, run_id="run-1")
    write_partial_capture(tmp_path, run_id="run-2")

    completed = _run("scan-capture", "--capture-root", str(tmp_path))

    assert completed.returncode == 0, completed.stderr
    report = CaptureScanReport.model_validate_json(completed.stdout)
    assert [(r.run_id, r.classification) for r in report.runs] == [
        ("run-1", CaptureClass.FINALIZED_WITH_RECEIPT),
        ("run-2", CaptureClass.CAPTURE_UNFINISHED),
    ]
    assert json.loads(completed.stdout) == report.model_dump(mode="json")


def test_cli_scan_capture_missing_root_exits_nonzero(tmp_path):
    completed = _run("scan-capture", "--capture-root", str(tmp_path / "nowhere"))

    assert completed.returncode == 1
    assert "scan-capture failed" in completed.stderr


def _source_tree(root: Path) -> Path:
    for path, data in {
        "README.md": b"# tiny\n",
        "data/chunk-000/file-000.parquet": b"PAR1",
        "meta/info.json": b'{"fps": 10}',
        "notes/skip.txt": b"not selected",
    }.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    return root


def _publish_snapshot_args(source_dir: Path, *extra: str) -> list[str]:
    return [
        "publish-snapshot",
        "--source-dir",
        str(source_dir),
        "--format",
        "lerobot",
        "--upstream",
        "lerobot/tiny",
        "--upstream-revision",
        "rev-1",
        # Given unsorted: the selection's canonical form is sorted.
        "--include",
        "meta/**",
        "--include",
        "README.md",
        "--include",
        "data/**",
        "--annotation",
        "license=mit",
        *extra,
    ]


def test_publish_snapshot_prints_the_registration_input_and_converges(
    store_root, tmp_path
) -> None:
    from sceneops_core.sources import (
        load_canonical_source_content_inventory,
        load_canonical_source_snapshot_descriptor,
    )

    source_dir = _source_tree(tmp_path / "source")
    env = _publisher_env(store_root)

    first = _run(*_publish_snapshot_args(source_dir), env=env)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["descriptor_uri"] == str(
        store_root / "sources" / "snapshots" / result["snapshot_id"] / "descriptor.json"
    )
    assert (result["file_count"], result["files_written"]) == (3, 3)
    assert result["inventory_written"] and result["descriptor_written"]
    descriptor = load_canonical_source_snapshot_descriptor(
        Path(result["descriptor_uri"]).read_bytes()
    )
    assert descriptor.declaration.selection.include == (
        "README.md",
        "data/**",
        "meta/**",
    )
    assert descriptor.annotations == {"license": "mit"}
    inventory = load_canonical_source_content_inventory(
        Path(result["inventory_uri"]).read_bytes()
    )
    assert [e.path for e in inventory.files] == [
        "README.md",
        "data/chunk-000/file-000.parquet",
        "meta/info.json",
    ]
    # The local source path is retrieval history, never stored.
    assert str(source_dir).encode() not in Path(result["descriptor_uri"]).read_bytes()

    second = _run(*_publish_snapshot_args(source_dir), env=env)
    assert second.returncode == 0, second.stderr
    again = json.loads(second.stdout)
    assert (again["files_written"], again["files_reused"]) == (0, 3)
    assert not again["inventory_written"] and not again["descriptor_written"]
    assert again["descriptor_checksum"] == result["descriptor_checksum"]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--include", "nothing/**"], "match no file"),
        (["--include", "data/[ab]"], "reserved characters"),
        (["--annotation", "license"], "KEY=VALUE"),
    ],
)
def test_publish_snapshot_refuses_bad_declarations_and_writes_nothing(
    store_root, tmp_path, extra, message
) -> None:
    source_dir = _source_tree(tmp_path / "source")

    completed = _run(
        *_publish_snapshot_args(source_dir, *extra), env=_publisher_env(store_root)
    )

    assert completed.returncode == 1
    assert "publish-snapshot failed" in completed.stderr
    assert message in completed.stderr
    assert completed.stdout == ""
    assert list(store_root.iterdir()) == []


def test_publish_snapshot_refuses_another_descriptor_for_a_published_declaration(
    store_root, tmp_path
) -> None:
    source_dir = _source_tree(tmp_path / "source")
    env = _publisher_env(store_root)
    assert _run(*_publish_snapshot_args(source_dir), env=env).returncode == 0

    (source_dir / "meta" / "info.json").write_bytes(b'{"fps": 30}')
    completed = _run(*_publish_snapshot_args(source_dir), env=env)

    assert completed.returncode == 1
    assert "SnapshotPublicationConflictError" in completed.stderr
    assert "one declared revision, two byte sets" in completed.stderr
