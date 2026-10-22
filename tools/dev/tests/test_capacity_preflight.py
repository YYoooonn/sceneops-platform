"""The capacity preflight refuses a workload that the Docker VM cannot hold, and never
passes without a measurement (no Docker: `docker` is a stub on PATH)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "dev" / "capacity_preflight.py"
_SPEC = importlib.util.spec_from_file_location("capacity_preflight", SCRIPT)
assert _SPEC and _SPEC.loader
preflight = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = preflight
_SPEC.loader.exec_module(preflight)

GIB = preflight.GIB
LOCK = json.loads(
    (REPO_ROOT / "config/reference/nuscenes-mini-v1/corpus.lock.json").read_text()
)
CORPUS = json.loads(
    (REPO_ROOT / "config/reference/nuscenes-mini-v1/corpus.json").read_text()
)


def test_recording_import_stores_the_recording_and_its_payloads_only():
    e = preflight.estimate("recording_import", [100, 300])
    assert e.minio_bytes == 400 + 340  # (1 + 0.85) x 400
    assert e.kafka_bytes == 0
    assert e.capture_peak_bytes == 0


def test_streaming_adds_the_retained_kafka_log_and_one_capture_at_a_time():
    e = preflight.estimate("streaming_acquisition", [100, 300])
    assert e.minio_bytes == 740
    assert e.kafka_bytes == 532  # 1.33 x 400: the log is kept for the whole run
    assert e.capture_peak_bytes == 300  # the largest capture, not their sum


def test_required_space_is_the_growth_with_margin_plus_wal_and_floor():
    e = preflight.estimate("streaming_acquisition", [GIB])
    growth = e.minio_bytes + e.kafka_bytes + e.capture_peak_bytes
    assert e.required_free_bytes == (
        -(-growth * 5 // 4) + preflight.POSTGRES_RESERVE_BYTES + preflight.FLOOR_BYTES
    )


def test_the_estimate_is_not_below_what_was_measured():
    """The 10-fixture Recording Import baseline stored 7.22 GB of objects in MinIO and
    the streamed scene-0061 655.3 MB (bucket listings); neither estimate may be lower."""
    sizes = preflight.resolve_fixtures(
        CORPUS, LOCK, fixtures=[], scope="nuscenes-mini-full-10"
    )
    assert len(sizes) == 10
    e = preflight.estimate("recording_import", list(sizes.values()))
    assert e.minio_bytes >= 4_068_158_098 + 3_156_445_716
    one = preflight.estimate(
        "streaming_acquisition",
        [LOCK["fixtures"]["scene-0061"]["recording"]["size_bytes"]],
    )
    assert one.minio_bytes >= 655_263_859
    assert one.kafka_bytes >= 461_064 * 1024  # the topic's log directory, du -sk


def test_a_workload_that_does_not_fit_the_docker_vm_is_refused():
    e = preflight.estimate("streaming_acquisition", [4 * GIB])
    ok, reason = preflight.decide(
        e,
        docker_vm_free_bytes=e.required_free_bytes - 1,
        host_free_bytes=100 * GIB,
    )
    assert not ok
    assert "Docker VM" in reason
    ok, _ = preflight.decide(
        e, docker_vm_free_bytes=e.required_free_bytes, host_free_bytes=100 * GIB
    )
    assert ok


def test_the_host_volume_must_keep_its_floor():
    e = preflight.estimate("recording_import", [GIB])
    ok, reason = preflight.decide(
        e, docker_vm_free_bytes=100 * GIB, host_free_bytes=preflight.FLOOR_BYTES - 1
    )
    assert not ok
    assert "host" in reason


def test_scopes_and_fixtures_resolve_through_the_lock():
    assert preflight.resolve_fixtures(CORPUS, LOCK, fixtures=[], scope="smoke-1") == {
        "scene-0061": LOCK["fixtures"]["scene-0061"]["recording"]["size_bytes"]
    }
    with pytest.raises(preflight.PreflightError):
        preflight.resolve_fixtures(CORPUS, LOCK, fixtures=["scene-9999"], scope=None)
    with pytest.raises(preflight.PreflightError):
        preflight.resolve_fixtures(CORPUS, LOCK, fixtures=[], scope="no-such-scope")


def test_df_output_is_parsed_from_the_data_line():
    out = (
        "Filesystem     1024-blocks     Used Available Capacity Mounted on\n"
        "/dev/vda1        61202244 43000000  14500000      75% /var/lib/postgresql/data\n"
    )
    assert preflight.parse_df_available(out) == 14_500_000 * 1024
    with pytest.raises(preflight.PreflightError):
        preflight.parse_df_available("df: no such file\n")


# ── the command ──────────────────────────────────────────────────────────────


def _run(tmp_path: Path, docker_script: str, *args: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\n" + docker_script)
    docker.chmod(0o755)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def _df(available_kib: int) -> str:
    return (
        "echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        f"echo '/dev/vda1 61202244 1 {available_kib} 1% /var/lib/postgresql/data'\n"
    )


def test_the_command_passes_a_workload_that_fits(tmp_path):
    result = _run(
        tmp_path,
        _df(50 * 1024 * 1024),
        "--mode",
        "streaming_acquisition",
        "--fixture",
        "scene-0061",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema"] == "sceneops.capacity_preflight/1"
    assert report["ok"] is True
    assert report["workload"]["fixtures"] == ["scene-0061"]
    assert report["measured"]["docker_vm_free_bytes"] == 50 * GIB


def test_the_command_refuses_a_workload_that_does_not_fit(tmp_path):
    result = _run(
        tmp_path,
        _df(4 * 1024 * 1024),
        "--mode",
        "streaming_acquisition",
        "--scope",
        "nuscenes-mini-full-10",
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert "refused" in result.stderr


def test_the_command_never_passes_without_a_measurement(tmp_path):
    result = _run(
        tmp_path,
        "echo 'No such container' >&2; exit 1\n",
        "--mode",
        "recording_import",
        "--scope",
        "smoke-1",
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "cannot measure" in result.stderr


# ── source snapshots ─────────────────────────────────────────────────────────

SNAPSHOTS = json.loads(
    (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
)


def test_a_snapshot_stores_its_content_once_and_nothing_else():
    e = preflight.estimate("source_snapshot", [100, 300])
    assert (e.minio_bytes, e.kafka_bytes, e.capture_peak_bytes) == (400, 0, 0)


def test_a_snapshot_that_does_not_fit_the_docker_vm_is_refused():
    """The M3.0 estimate for pusht + the scene-0061 slice: 0.49 GiB of content
    needs ~3.6 GiB free with the margin, WAL reserve and floor."""
    e = preflight.estimate("source_snapshot", [7_684_382, 519_531_462])
    assert 3.6 * GIB < e.required_free_bytes < 3.7 * GIB
    ok, reason = preflight.decide(
        e, docker_vm_free_bytes=e.required_free_bytes - 1, host_free_bytes=100 * GIB
    )
    assert not ok and "Docker VM" in reason


def test_snapshot_sizes_come_from_the_selected_local_files(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    for path, size in {"a/x.bin": 3, "a/y.bin": 5, "b/z.bin": 7}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"0" * size)
    monkeypatch.setattr(preflight, "REPO_ROOT", tmp_path)
    document = {"snapshots": {"s": {"local_root": "tree", "include": ["a/**"]}}}

    assert preflight.resolve_snapshots(document, ["s"]) == {"s": [3, 5]}
    with pytest.raises(preflight.PreflightError, match="unknown snapshots"):
        preflight.resolve_snapshots(document, ["t"])
    document["snapshots"]["s"]["include"] = ["c/**"]
    with pytest.raises(preflight.PreflightError, match="match no file"):
        preflight.resolve_snapshots(document, ["s"])


def test_the_pusht_snapshot_declares_its_pinned_raw_source():
    """The reference declaration of pusht is the raw-source pin, so the bytes it
    preserves are the locked ones."""
    entry = SNAPSHOTS["snapshots"]["lerobot-pusht"]
    pin = json.loads(
        (REPO_ROOT / "config/reference/lerobot-pusht/source.json").read_text()
    )
    assert entry["local_root"] == pin["local_root"]
    assert entry["upstream"] == pin["origin"]["repo"]
    assert entry["upstream_revision"] == pin["origin"]["revision"]
    assert sorted(entry["include"]) == sorted(pin["include"])
    assert entry["annotations"] == {"license": pin["license"]}


def test_snapshot_mode_requires_snapshots_and_only_it_takes_them(tmp_path):
    result = _run(
        tmp_path,
        _df(50 * 1024 * 1024),
        "--mode",
        "source_snapshot",
        "--scope",
        "smoke-1",
    )
    assert result.returncode == 2
    result = _run(
        tmp_path, _df(50 * 1024 * 1024), "--mode", "recording_import", "--snapshot", "x"
    )
    assert result.returncode == 2
