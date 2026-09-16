from __future__ import annotations

from enum import StrEnum


class EpisodeStatus(StrEnum):
    CREATED = "created"
    BUILT = "built"
    REGISTERED = "registered"
    # Reserved for Phase 4 (episode quality) — not set by any handler yet.
    VALIDATED = "validated"
    FAILED = "failed"


class EpisodeOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
