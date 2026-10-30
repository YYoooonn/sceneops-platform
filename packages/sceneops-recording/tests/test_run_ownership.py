"""Exclusive local ownership of one RobotRun by one Capture attempt: an advisory
``flock`` per run id on the capture root (``sceneops_recording.capture.ownership``).

The lock is real (a second open file description of the same file, and a
separate process) -- nothing here is mocked.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from sceneops_recording.capture.finalize import run_lock_path
from sceneops_recording.capture.ownership import RunAlreadyOwnedError, own_run

_HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from sceneops_recording.capture.ownership import own_run

    with own_run(Path(sys.argv[1]), sys.argv[2]):
        print("held", flush=True)
        time.sleep(600)
    """
)


def _spawn_holder(root: Path, run_id: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(root), run_id],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_second_owner_of_the_same_run_fails_at_once(tmp_path):
    with own_run(tmp_path, "run-1"):
        started = time.monotonic()
        with pytest.raises(RunAlreadyOwnedError, match=rf"pid {os.getpid()}"):
            with own_run(tmp_path, "run-1"):
                pytest.fail("a second owner must not enter")
        assert time.monotonic() - started < 1.0


def test_ownership_is_released_on_normal_exit_and_on_error(tmp_path):
    with own_run(tmp_path, "run-1"):
        pass
    with pytest.raises(RuntimeError, match="boom"):
        with own_run(tmp_path, "run-1"):
            raise RuntimeError("boom")
    with own_run(tmp_path, "run-1"):
        pass


def test_different_runs_do_not_exclude_each_other(tmp_path):
    with own_run(tmp_path, "run-1"), own_run(tmp_path, "run-2"):
        pass


def test_the_lock_is_scoped_to_the_capture_root(tmp_path):
    with own_run(tmp_path / "a", "run-1"), own_run(tmp_path / "b", "run-1"):
        pass


def test_lock_file_persists_after_release(tmp_path):
    with own_run(tmp_path, "run-1"):
        pass
    assert run_lock_path(tmp_path, "run-1").is_file()


def test_another_process_is_excluded_and_a_killed_owner_releases_the_run(tmp_path):
    holder = _spawn_holder(tmp_path, "run-1")
    try:
        with pytest.raises(RunAlreadyOwnedError, match=rf"pid {holder.pid}"):
            with own_run(tmp_path, "run-1"):
                pytest.fail("the run is owned by another process")

        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=10)

        # The kernel dropped the lock with the dead process: no stale lock
        # to clean up and no timeout to wait for.
        with own_run(tmp_path, "run-1"):
            pass
    finally:
        holder.kill()
        holder.wait()
        holder.stdout.close()
