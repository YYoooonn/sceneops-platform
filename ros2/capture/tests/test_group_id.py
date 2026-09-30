"""Unit tests for group_id.py: deterministic, run-scoped Kafka
consumer-group derivation (Phase 6.6.1).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_id import derive_capture_group_id  # noqa: E402

_BASE = "sceneops-mcap-capture"


def test_same_run_id_derives_the_same_group_every_call() -> None:
    a = derive_capture_group_id(base=_BASE, robot_run_id="run-001")
    b = derive_capture_group_id(base=_BASE, robot_run_id="run-001")
    assert a == b


def test_different_run_ids_derive_different_groups() -> None:
    a = derive_capture_group_id(base=_BASE, robot_run_id="run-001")
    b = derive_capture_group_id(base=_BASE, robot_run_id="run-002")
    assert a != b


def test_configured_base_is_respected_and_prefixes_the_result() -> None:
    group_id = derive_capture_group_id(base=_BASE, robot_run_id="run-001")
    assert group_id.startswith(_BASE + "-")

    other_base_group_id = derive_capture_group_id(
        base="some-other-base", robot_run_id="run-001"
    )
    assert other_base_group_id.startswith("some-other-base-")
    # Same robot_run_id, different base -> different group (base is part
    # of the derivation, not ignored).
    assert other_base_group_id != group_id


def test_stable_across_many_repeated_calls() -> None:
    results = {
        derive_capture_group_id(base=_BASE, robot_run_id="run-repeat-check")
        for _ in range(50)
    }
    assert len(results) == 1


def test_unusual_characters_in_run_id_are_sanitized_safely() -> None:
    group_id = derive_capture_group_id(
        base=_BASE, robot_run_id="run/with spaces/and:colons#and?slashes"
    )
    # Kafka-safe: only [a-zA-Z0-9_.-], no raw '/', ' ', ':', '#', '?'.
    assert all(c.isalnum() or c in "_.-" for c in group_id)
    # Still deterministic despite the unusual input.
    again = derive_capture_group_id(
        base=_BASE, robot_run_id="run/with spaces/and:colons#and?slashes"
    )
    assert group_id == again


def test_long_run_id_produces_a_bounded_length_group_id() -> None:
    long_run_id = "run-" + ("x" * 500)
    group_id = derive_capture_group_id(base=_BASE, robot_run_id=long_run_id)
    # base + '-' + <=32-char slug + '-' + 16-char digest, generously bounded.
    assert len(group_id) <= len(_BASE) + 1 + 32 + 1 + 16


def test_run_ids_that_sanitize_to_the_same_slug_still_derive_different_groups() -> None:
    """The slug alone is lossy (not collision-free) -- the digest suffix
    is what actually guarantees uniqueness. Two inputs that sanitize to
    an identical slug must still diverge because their raw bytes (and
    thus their digests) differ."""
    a = derive_capture_group_id(base=_BASE, robot_run_id="run/A")
    b = derive_capture_group_id(base=_BASE, robot_run_id="run.A")
    assert a != b


def test_empty_or_fully_unsafe_run_id_still_produces_a_valid_group_id() -> None:
    group_id = derive_capture_group_id(base=_BASE, robot_run_id="###///???")
    assert group_id.startswith(_BASE + "-")
    assert len(group_id) > len(_BASE) + 1


def test_no_builtin_hash_used_result_is_process_independent() -> None:
    """Sanity check on the contract, not just the implementation: the
    derivation must not depend on Python's randomized hash() -- verified
    indirectly by confirming the result matches a hand-computed sha256
    digest, which is only possible if a stable digest (not hash()) is
    what drives uniqueness."""
    import hashlib

    robot_run_id = "run-verify-digest"
    group_id = derive_capture_group_id(base=_BASE, robot_run_id=robot_run_id)
    expected_digest = hashlib.sha256(robot_run_id.encode("utf-8")).hexdigest()[:16]
    assert expected_digest in group_id
