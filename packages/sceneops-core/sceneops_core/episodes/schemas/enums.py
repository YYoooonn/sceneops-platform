from __future__ import annotations

from enum import StrEnum


class EpisodeOutcome(StrEnum):
    """Outcome vocabulary of derived learning workflows (AlignedEpisode,
    curation, learning exports). A canonical EpisodeManifest never carries
    an outcome: it holds only recorded event values (ADR-007 §31.5)."""

    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
