"""`run_pipeline` (tools/e2e/lib.sh) with `{"force": false}` dispatches only a run that
can still be executed.

An identical request returns the existing pending, queued, running or succeeded
PipelineRun. A caller that was interrupted while its pipeline ran (a killed baseline
bootstrap) and is run again gets that running run back; the API refuses to execute
a running or succeeded run (HTTP 400), so it must be waited for, not dispatched.

`curl` is replaced by a stub on PATH that records its calls and serves canned
responses, so the real shell function runs without an API.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "tools" / "e2e" / "lib.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")

STUB = """\
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
case "$*" in
  *"-X POST"*"/pipelines/runs/"*"/execute"*) echo '{"execution": {}}' ;;
  *"-X POST"*"/pipelines/runs"*) cat "$STUB_DIR/created.json" ;;
  *"/pipelines/runs/"*) echo '{"pipelineRun": {"pipelineRunId": "pipe-1", "status": "succeeded"}}' ;;
esac
"""


def _run_pipeline(tmp_path: Path, returned_status: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STUB)
    curl.chmod(0o755)
    (tmp_path / "created.json").write_text(
        json.dumps(
            {"pipelineRun": {"pipelineRunId": "pipe-1", "status": returned_status}}
        )
    )
    log = tmp_path / "curl.log"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'REPO_ROOT="{REPO_ROOT}"; source "{LIB}"; '
            "run_pipeline http://api recording_scene_building '' '' '{}' '{\"force\": false}'",
        ],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "CURL_LOG": str(log),
            "STUB_DIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "pipe-1"
    return log.read_text().splitlines()


def _executed(calls: list[str]) -> bool:
    return any("/pipelines/runs/pipe-1/execute" in call for call in calls)


def _polled(calls: list[str]) -> bool:
    return any(
        "-X POST" not in call and "/pipelines/runs/pipe-1" in call for call in calls
    )


@pytest.mark.parametrize("status", ["pending", "queued"])
def test_a_run_not_yet_running_is_dispatched_and_waited_for(tmp_path, status):
    calls = _run_pipeline(tmp_path, status)
    assert _executed(calls)
    assert _polled(calls)


def test_a_running_run_is_waited_for_and_not_dispatched(tmp_path):
    calls = _run_pipeline(tmp_path, "running")
    assert not _executed(calls)
    assert _polled(calls)


def test_a_succeeded_run_is_neither_dispatched_nor_polled(tmp_path):
    calls = _run_pipeline(tmp_path, "succeeded")
    assert not _executed(calls)
    assert not _polled(calls)
