"""Episode readiness, derived from validation run records (ADR-007 §13.4).

Readiness is never stored on an EpisodeRecord. It is derived from the latest
validation run that assessed the revision in question; a run for any other
revision is ignored, so replacing an Episode's manifest resets its readiness
to ``unknown`` until the new revision is validated.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import TypeVar

from sceneops_core.runs.schemas import RunStatus

from .schemas.runs import EpisodeProfileRunRecord, EpisodeValidationRunRecord

_BLOCKING_VALIDATION_STATUSES = frozenset({"failed", "error"})

R = TypeVar("R", EpisodeValidationRunRecord, EpisodeProfileRunRecord)


class EpisodeReadiness(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


def latest_run_for_revision(
    runs: Iterable[R],
    *,
    episode_id: str,
    manifest_artifact_id: str,
    manifest_checksum: str,
) -> R | None:
    """The first succeeded run of exactly this revision in ``runs``, which
    callers pass newest first (as run repositories list them)."""
    for run in runs:
        if (
            run.episode_id == episode_id
            and run.status == RunStatus.SUCCEEDED
            and run.assessed(
                manifest_artifact_id=manifest_artifact_id,
                manifest_checksum=manifest_checksum,
            )
        ):
            return run
    return None


def derive_episode_readiness(
    validation_run: EpisodeValidationRunRecord | None,
) -> EpisodeReadiness:
    if validation_run is None or validation_run.status != RunStatus.SUCCEEDED:
        return EpisodeReadiness.UNKNOWN
    status = (validation_run.validation_status or "").lower()
    if validation_run.should_block_pipeline or status in _BLOCKING_VALIDATION_STATUSES:
        return EpisodeReadiness.BLOCKED
    if status == "warning":
        return EpisodeReadiness.WARNING
    if status == "ready":
        return EpisodeReadiness.READY
    return EpisodeReadiness.UNKNOWN


__all__ = [
    "EpisodeReadiness",
    "derive_episode_readiness",
    "latest_run_for_revision",
]
