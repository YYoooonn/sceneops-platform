"""The streaming launcher (tools/baselines/streaming/streaming_lib.sh) hands the
bridge's acknowledged RUN_START position to Capture.

`docker` is replaced by a stub on PATH that records its calls and serves canned
logs, so these tests run the real shell functions without containers: they prove
the hand-off (what is parsed, what Capture is started with, in which order, and
that nothing starts from a position nobody acknowledged). The position itself
coming from a real broker is proven by tests/streaming.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "tools" / "baselines" / "streaming" / "streaming_lib.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")

STUB = """\
#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
case "$1" in
  rm) ;;
  wait) echo 0 ;;
  logs)
    case "$2" in
      bridge-t) cat "$STUB_DIR/bridge.log" ;;
      capture-t) cat "$STUB_DIR/capture.log" ;;
    esac ;;
  inspect)
    if [ "$2" = -f ]; then cat "$STUB_DIR/running"; else
      echo '[{"State":{"StartedAt":"2026-01-01T00:00:00.5Z","FinishedAt":"2026-01-01T00:00:03.0Z"}}]'
    fi ;;
  compose)
    case "$*" in
      *"--entrypoint sh"*) echo ok ;;
      *"reference replay"*)
        echo 'replay_source {"path": "/reference/x"}'
        echo 'replay_summary {"elapsed_seconds": 1.5}' ;;
    esac ;;
esac
"""

RUN_STARTED = {
    "event": "run_started",
    "robot_run_id": "run-1",
    "topic": "sceneops.robot.telemetry.v1",
    "partition": 2,
    "offset": 18432,
    "start_position": "sceneops.robot.telemetry.v1:2:18432",
}
READY = "streaming bridge ready: robot_id=r robot_run_id=run-1"
CAPTURE_SUMMARY = {
    "path": "/recordings/x.mcap",
    "receipt_path": "/recordings/r.json",
    "message_count": 1,
    "sha256": "0" * 64,
    "per_channel_counts": {},
}


@pytest.fixture
def stub(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(STUB)
    docker.chmod(0o755)
    (tmp_path / "running").write_text("true\n")
    (tmp_path / "capture.log").write_text(
        "capture_summary " + json.dumps(CAPTURE_SUMMARY) + "\n"
    )
    return tmp_path


def _run(
    stub: Path, script: str, timeout: float = 30, **extra: str
) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{stub / 'bin'}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "DOCKER_LOG": str(stub / "docker.log"),
        "STUB_DIR": str(stub),
        "ENV_FILE": "unused",
        "REFERENCE_CORPUS": "corpus",
        **extra,
    }
    prelude = f"""
        set -uo pipefail
        fail() {{ echo "FAIL: $*" >&2; exit 1; }}
        source {LIB}
    """
    return subprocess.run(
        ["bash", "-c", textwrap.dedent(prelude) + textwrap.dedent(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _docker_calls(stub: Path) -> list[str]:
    log = stub / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def _acquire(stub: Path, **extra: str) -> subprocess.CompletedProcess:
    return _run(
        stub,
        """
        streaming_acquire '{"fixture_id": "f"}' run-1 robot-1 /recordings/c bridge-t capture-t
        echo "START_POSITION=$START_POSITION"
        """,
        **extra,
    )


DISPOSABLE = {
    "STREAMING_RUNTIME": "disposable",
    "REPO_ROOT": str(REPO_ROOT),
    "TEST_DATABASE_URL": "postgresql+asyncpg://u:p@postgres:5432/sceneops_test",
    "TEST_ARTIFACT_ROOT_URI": "s3://sceneops-test/artifacts",
    "TEST_API_PORT": "18000",
}


def test_wait_run_started_returns_the_position_among_other_output(stub):
    (stub / "bridge.log").write_text(
        "2026 INFO sceneops: starting\n"
        "run_started " + json.dumps(RUN_STARTED) + "\n"
        "run_started_but_not_a_line\n"
        + READY + "\n"
    )

    result = _run(stub, "streaming_wait_run_started bridge-t 3")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == RUN_STARTED["start_position"]


def test_wait_run_started_fails_when_the_bridge_exits_without_a_position(stub):
    (stub / "bridge.log").write_text("RUN_START was not acknowledged\n")
    (stub / "running").write_text("false\n")

    result = _run(stub, "streaming_wait_run_started bridge-t 3")

    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_capture_is_started_with_the_exact_acknowledged_position(stub):
    (stub / "bridge.log").write_text(
        "run_started " + json.dumps(RUN_STARTED) + "\n" + READY + "\n"
    )

    result = _acquire(stub)

    assert result.returncode == 0, result.stderr
    assert f"START_POSITION={RUN_STARTED['start_position']}" in result.stdout
    calls = _docker_calls(stub)
    capture_start = [c for c in calls if "-T capture" in c and "--robot-id" in c]
    assert len(capture_start) == 1
    assert capture_start[0].endswith(
        f"--start-position {RUN_STARTED['start_position']}"
    )
    # Bridge first, then Capture, then the replay that produces the telemetry.
    positions = {
        name: next(i for i, c in enumerate(calls) if marker in c)
        for name, marker in {
            "bridge": "-T streaming-bridge",
            "capture": "-T capture",
            "replay": "reference replay",
        }.items()
    }
    assert positions["bridge"] < positions["capture"] < positions["replay"]


def test_no_capture_and_no_replay_start_when_run_start_was_never_acknowledged(stub):
    (stub / "bridge.log").write_text("RUN_START was not acknowledged\n")
    (stub / "running").write_text("false\n")

    result = _acquire(stub)

    assert result.returncode != 0
    assert "acknowledged RUN_START" in result.stderr
    calls = _docker_calls(stub)
    assert any("-T streaming-bridge" in c for c in calls)
    assert not any("-T capture" in c for c in calls)
    assert not any("reference replay" in c for c in calls)


def test_a_late_run_started_line_is_waited_for(stub):
    (stub / "bridge.log").write_text(READY + "\n")
    result = _run(
        stub,
        f"""
        ( sleep 1.5; echo 'run_started {json.dumps(RUN_STARTED)}' >> "$STUB_DIR/bridge.log" ) &
        streaming_wait_run_started bridge-t 10
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == RUN_STARTED["start_position"]


def test_the_disposable_runtime_runs_every_step_on_its_own_services(stub):
    (stub / "bridge.log").write_text(
        "run_started " + json.dumps(RUN_STARTED) + "\n" + READY + "\n"
    )

    result = _acquire(stub, **DISPOSABLE)

    assert result.returncode == 0, result.stderr
    compose = [c for c in _docker_calls(stub) if c.startswith("compose ")]
    assert compose
    for call in compose:
        # Never the reference project: always the disposable one, both its files.
        assert call.startswith("compose -p sceneops-test -f "), call
        assert "compose/test-runtime.yaml" in call and "compose/test-streaming.yaml" in call
    joined = "\n".join(compose)
    for service in ("test-streaming-bridge", "test-capture", "test-dataset-replay"):
        assert f"-T {service}" in joined, service
    assert "-T capture " not in joined and "-T streaming-bridge " not in joined


def test_the_disposable_runtime_refuses_to_start_outside_its_runner(stub):
    result = _run(
        stub,
        "echo reached",
        STREAMING_RUNTIME="disposable",
        REPO_ROOT=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "reached" not in result.stdout
    assert "disposable_env.py run --streaming" in result.stderr
    assert _docker_calls(stub) == []


def test_an_unknown_runtime_is_refused(stub):
    result = _run(stub, "echo reached", STREAMING_RUNTIME="golden")
    assert result.returncode == 2
    assert "reached" not in result.stdout
