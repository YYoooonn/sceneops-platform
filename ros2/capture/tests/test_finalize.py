"""Unit tests for finalize.py: temp/final bag directory lifecycle.

Runs only inside the ros2 container (matches the rest of ros2/capture's
flat-script import convention). Pure filesystem logic -- no Kafka, no
real MCAP writer needed, dummy files stand in for bag contents.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from finalize import (  # noqa: E402
    FinalBagExistsError,
    final_bag_path,
    finalize_bag,
    partial_bag_path,
    prepare_partial_bag_dir,
)


def test_prepare_partial_bag_dir_returns_nonexistent_path_with_ready_parent(
    tmp_path,
) -> None:
    # The writer creates `partial` itself and refuses an existing
    # directory, so this function only guarantees the path doesn't exist
    # yet and its parent (`.partial/`) does.
    partial = prepare_partial_bag_dir(tmp_path, "run-1")
    assert partial == partial_bag_path(tmp_path, "run-1")
    assert not partial.exists()
    assert partial.parent.is_dir()


def test_prepare_partial_bag_dir_discards_stale_partial_contents(tmp_path) -> None:
    partial = prepare_partial_bag_dir(tmp_path, "run-1")
    partial.mkdir()
    (partial / "stale_leftover.mcap").write_bytes(b"stale bytes from a crashed attempt")

    partial_again = prepare_partial_bag_dir(tmp_path, "run-1")

    assert not partial_again.exists()


def test_prepare_partial_bag_dir_does_not_disturb_other_run_ids(tmp_path) -> None:
    partial_a = prepare_partial_bag_dir(tmp_path, "run-a")
    partial_a.mkdir()
    (partial_a / "a.mcap").write_bytes(b"run a data")

    prepare_partial_bag_dir(tmp_path, "run-b")

    assert (partial_a / "a.mcap").exists()


def test_finalize_bag_atomically_moves_partial_to_final(tmp_path) -> None:
    partial = prepare_partial_bag_dir(tmp_path, "run-1")
    partial.mkdir()
    (partial / "run-1_0.mcap").write_bytes(b"payload")
    (partial / "metadata.yaml").write_bytes(b"meta")

    final = finalize_bag(tmp_path, "run-1")

    assert final == final_bag_path(tmp_path, "run-1")
    assert not partial.exists()
    assert final.is_dir()
    assert (final / "run-1_0.mcap").read_bytes() == b"payload"


def test_finalize_bag_refuses_to_overwrite_existing_final(tmp_path) -> None:
    partial = prepare_partial_bag_dir(tmp_path, "run-1")
    partial.mkdir()
    (partial / "run-1_0.mcap").write_bytes(b"first attempt")
    finalize_bag(tmp_path, "run-1")

    partial_again = prepare_partial_bag_dir(tmp_path, "run-1")
    partial_again.mkdir()
    (partial_again / "run-1_0.mcap").write_bytes(b"second attempt")

    with pytest.raises(FinalBagExistsError):
        finalize_bag(tmp_path, "run-1")

    # The original finalized bag must be untouched by the refused attempt.
    final = final_bag_path(tmp_path, "run-1")
    assert (final / "run-1_0.mcap").read_bytes() == b"first attempt"
    # The second attempt's partial is left in place, not silently deleted --
    # a caller can inspect/discard it themselves on the next invocation.
    assert partial_again.exists()
