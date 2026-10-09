"""Exclusive local ownership of one RobotRun by one Capture attempt.

An advisory ``flock`` on ``<output_root>/.locks/<robot_run_id>.lock`` is held for
the whole attempt, from before ``.partial/<run>`` is touched until after the
Kafka commit. A second attempt for the same run on the same capture root fails
at once with ``RunAlreadyOwnedError`` instead of discarding the live peer's
partial bag (``prepare_partial_bag_dir``) or racing it to ``finalize_bag``.

The lock belongs to the open file description, so the kernel drops it when the
holder exits -- normally, on a crash or on SIGKILL -- and a restarted attempt
needs no stale-lock cleanup or timeout. Lock files are never deleted: unlinking
one while a peer waits to open it would let two attempts hold "the" lock on
different inodes. The files are empty markers (the holder's pid is written for
diagnostics only) and ``scan_capture_volume`` ignores the directory.

Scope: attempts that share one filesystem on which ``flock`` is honoured (a
local disk or one Docker volume). It is not a cluster-wide lease; which
instance should capture a run is the launcher's decision (Kafka decides
nothing under ``assign()``).
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .finalize import run_lock_path


class RunAlreadyOwnedError(RuntimeError):
    """Another Capture attempt holds this robot_run_id on this capture root."""


@contextmanager
def own_run(output_root: Path, robot_run_id: str) -> Iterator[None]:
    lock_path = run_lock_path(output_root, robot_run_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunAlreadyOwnedError(
                f"robot_run_id={robot_run_id!r} is being captured by another "
                f"attempt (holder pid {_holder_pid(fd)}): {lock_path}"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(fd)  # drops the lock


def _holder_pid(fd: int) -> str:
    try:
        text = os.pread(fd, 32, 0).decode().strip()
    except OSError:
        return "unknown"
    return text or "unknown"
