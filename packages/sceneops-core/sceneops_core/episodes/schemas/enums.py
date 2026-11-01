from __future__ import annotations

from enum import StrEnum


class EpisodeStreamRole(StrEnum):
    """What a recorded stream is to the Episode. ``event`` is reserved for
    task / event sources (``RecordingEpisodeBuildConfig.events``).
    ``transition`` is a per-frame signal about the step from a frame to the
    next (a LeRobot ``next.*`` feature): one occurrence per frame of the
    Episode's frame timeline, never an event; recordings have none."""

    OBSERVATION = "observation"
    STATE = "state"
    ACTION = "action"
    EVENT = "event"
    TRANSITION = "transition"


class EpisodeOutcome(StrEnum):
    """Outcome vocabulary of derived learning workflows (AlignedEpisode,
    curation, learning exports). A canonical EpisodeManifest never carries
    an outcome: it holds only recorded event values (ADR-007 §31.5)."""

    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
