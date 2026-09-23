"""Format-independent export invariants (SceneOps V2 Request 3.1 §8).

ExternalDatasetAdapter.export() already calls every function here on its own
output; they are exposed publicly so a future concrete adapter's own tests
(e.g. a LeRobot export's round-trip test) can reuse the same invariants
without redefining them.
"""

from __future__ import annotations

from collections.abc import Sequence

from sceneops_core.episodes.learning import EpisodeRef

from .errors import (
    EpisodeCountMismatchError,
    EpisodeRefTraceabilityError,
    StepCountMismatchError,
    StepOrderingError,
)
from .schemas import ExternalEpisode, ExternalExportReport


def validate_episode_count(report: ExternalExportReport) -> None:
    """Episode count preserved: exported_episode_count must equal the number
    of EpisodeRefs the export actually selected."""
    expected = len(report.source_episode_refs)
    if report.exported_episode_count != expected:
        raise EpisodeCountMismatchError(
            f"exported_episode_count={report.exported_episode_count} but "
            f"source_episode_refs has {expected} entries"
        )


def validate_step_count(
    report: ExternalExportReport, episodes: Sequence[ExternalEpisode]
) -> None:
    """Step count preserved: exported_step_count must equal the sum of every
    exported ExternalEpisode's step count."""
    expected = sum(len(episode.steps) for episode in episodes)
    if report.exported_step_count != expected:
        raise StepCountMismatchError(
            f"exported_step_count={report.exported_step_count} but exported "
            f"episodes contain {expected} steps in total"
        )


def validate_step_ordering(episode: ExternalEpisode) -> None:
    """Step ordering preserved: step_index runs exactly 0..len(steps)-1 in
    order, and timestamp_us strictly increases -- a SceneOps fixed-frequency
    Episode never repeats or reorders a timestamp."""
    previous_timestamp: int | None = None
    for expected_index, step in enumerate(episode.steps):
        if step.step_index != expected_index:
            raise StepOrderingError(
                f"{episode.episode_ref!r} step at position {expected_index} "
                f"has step_index={step.step_index}, expected {expected_index}"
            )
        if previous_timestamp is not None and step.timestamp_us <= previous_timestamp:
            raise StepOrderingError(
                f"{episode.episode_ref!r} step_index={step.step_index} has "
                f"timestamp_us={step.timestamp_us}, which does not strictly "
                f"increase from the previous step's timestamp_us="
                f"{previous_timestamp}"
            )
        previous_timestamp = step.timestamp_us


def validate_episode_ref_traceability(
    episodes: Sequence[ExternalEpisode], known_refs: Sequence[EpisodeRef]
) -> None:
    """EpisodeRef traceability retained: every exported ExternalEpisode must
    carry an EpisodeRef the source SceneOpsDataset actually exposes -- never
    a synthetic or ArtifactRecord-derived id."""
    known = set(known_refs)
    for episode in episodes:
        if episode.episode_ref not in known:
            raise EpisodeRefTraceabilityError(
                f"{episode.episode_ref!r} is not among the source dataset's "
                "exposed EpisodeRefs"
            )
