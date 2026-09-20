from __future__ import annotations

from enum import StrEnum


class EpisodeStatus(StrEnum):
    """Resource-registration lifecycle only — deliberately does not track
    validation/profile/quality state. See apps/api/app/domains/episodes/
    quality.py's module docstring (SceneOps V2 Request 17 §7): readiness is
    derived entirely from the latest EpisodeValidationRunRecord/
    EpisodeProfileRunRecord, never from this field. Stabilization Request 5
    removed BUILT/VALIDATED/FAILED — zero write sites, zero persisted rows,
    and a stale "reserved for Phase 4" comment on VALIDATED left over from
    before that design decision was made explicit."""

    CREATED = "created"
    REGISTERED = "registered"


class EpisodeOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
