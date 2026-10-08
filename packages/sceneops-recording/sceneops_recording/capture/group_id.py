"""Deterministic, run-scoped Kafka consumer-group derivation for durable
capture (Phase 6.6.1).

``SCENEOPS_STREAMING_KAFKA_CAPTURE_CONSUMER_GROUP_ID``
(``StreamingSettings.capture_consumer_group_id`` if/when exposed, today
just ``capture_consumer.CAPTURE_CONSUMER_GROUP_ID``) is a **capture
consumer-group base**, not the literal Kafka ``group.id`` a capture
attempt uses. Every RobotRun capture derives its own group from that
base plus its ``robot_run_id``, so committed offsets are never shared
across independent RobotRuns.

Phase 6.6's real-Kafka multi-RobotRun test found the concrete failure
this closes: with one shared, literal group across every capture, one
run's poll loop reading past another run's interleaved messages (to
reach its own target count) silently advanced the *shared* committed
offset past those other messages too -- a later, independent capture for
that other run could then miss its own early messages entirely.

Centralized here (not built ad hoc in capture code and scripts
separately) so anything that needs to know a run's own group --
``capture_consumer.py``, tests, ops/benchmark tooling querying Kafka
consumer-group lag -- derives it identically, every time.
"""

from __future__ import annotations

import hashlib
import re

_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")
_SLUG_MAX_LEN = 32
_DIGEST_LEN = 16  # hex chars = 64 bits -- collision-negligible at this scale


def derive_capture_group_id(*, base: str, robot_run_id: str) -> str:
    """One deterministic Kafka consumer group per ``(base, robot_run_id)``.

    - The SAME ``robot_run_id`` always derives the SAME group -- every
      retry, every process, every restart. A pure function of its
      inputs only: never Python's built-in ``hash()``, which is
      randomized per-process and would derive a different group every
      time the capture process restarts.
    - DIFFERENT ``robot_run_id``s derive DIFFERENT groups, guaranteed by
      a SHA-256 digest suffix (cryptographic collision resistance) --
      not by the human-readable slug prefix alone, which is cosmetic
      only and is NOT collision-free by itself (e.g. the robot_run_ids
      ``"run/A"`` and ``"run.A"`` both sanitize to the same slug; the
      digest is what actually keeps them apart).
    - Bounded length and Kafka-safe characters regardless of what
      ``robot_run_id`` itself contains.
    """
    digest = hashlib.sha256(robot_run_id.encode("utf-8")).hexdigest()[:_DIGEST_LEN]
    slug = _UNSAFE_CHARS.sub("-", robot_run_id)[:_SLUG_MAX_LEN].strip("-")
    if slug:
        return f"{base}-{slug}-{digest}"
    return f"{base}-{digest}"
