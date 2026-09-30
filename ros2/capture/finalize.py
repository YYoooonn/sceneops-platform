"""Temp/final MCAP bag directory lifecycle.

``rosbag2_py.SequentialWriter`` writes into a *directory* (``metadata.yaml``
plus one or more ``.mcap`` files), not a single file -- the unit that must
move atomically from "being written" to "durably captured" is therefore
that whole directory.

Layout, per ``robot_run_id``, under one capture ``output_root``:

    <output_root>/.partial/<robot_run_id>/   -- write target (in progress)
    <output_root>/<robot_run_id>/            -- finalized (atomically renamed)

``os.replace()`` is atomic only within a single filesystem, which is
guaranteed here because both paths share ``output_root``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class FinalBagExistsError(FileExistsError):
    """Refusing to overwrite an already-finalized bag for this robot_run_id."""


def partial_bag_path(output_root: Path, robot_run_id: str) -> Path:
    return output_root / ".partial" / robot_run_id


def final_bag_path(output_root: Path, robot_run_id: str) -> Path:
    return output_root / robot_run_id


def prepare_partial_bag_dir(output_root: Path, robot_run_id: str) -> Path:
    """Return a clean (non-existent) write target for a new capture
    attempt, ready for ``rosbag2_py.SequentialWriter`` to create itself
    -- the writer refuses to open into a directory that already exists,
    even an empty one, so this function does not create ``partial``
    itself, only guarantees it does not yet exist and that its parent
    does.

    A stale ``.partial`` directory from a previous crashed/interrupted
    attempt is never appended to or resumed -- it is discarded and
    recreated from scratch. The source of truth for what belongs in a
    capture is Kafka, replayed from the last *committed* offset (which,
    by construction of the commit-after-finalize ordering, is always
    before anything this stale partial could contain), not whatever
    partial bytes happen to already be on disk.
    """
    partial = partial_bag_path(output_root, robot_run_id)
    if partial.exists():
        shutil.rmtree(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    return partial


def finalize_bag(output_root: Path, robot_run_id: str) -> Path:
    """Atomically publish the partial bag as the final bag for this run.

    Must only be called after the writer is closed+fsynced and the
    written contents have been read back and validated (see
    ``validation.py``) -- this function performs no validation itself,
    only the atomic filesystem transition and the parent-directory fsync
    that makes that transition durable. Kafka offsets must only be
    committed after this returns successfully, never before.
    """
    partial = partial_bag_path(output_root, robot_run_id)
    final = final_bag_path(output_root, robot_run_id)

    if final.exists():
        raise FinalBagExistsError(
            f"final bag already exists for robot_run_id={robot_run_id!r}, "
            f"refusing to overwrite: {final}"
        )

    os.replace(partial, final)

    dir_fd = os.open(output_root, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    return final
