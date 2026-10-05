"""Scene readiness, derived from validation run records (ADR-007 §13.4).

Readiness is never stored on a SceneRecord. It is derived from the latest
validation run that assessed the revision in question; a run for any other
revision is ignored, so replacing a Scene's manifest resets its readiness
to ``unknown`` until the new revision is validated.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from sceneops_core.runs.schemas import RunStatus

from .schemas.runs import SceneValidationRunRecord

_BLOCKING_VALIDATION_STATUSES = frozenset({"failed", "error"})


class SceneReadiness(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


def latest_validation_for_revision(
    runs: Iterable[SceneValidationRunRecord],
    *,
    scene_id: str,
    manifest_artifact_id: str,
    manifest_checksum: str,
) -> SceneValidationRunRecord | None:
    """The first succeeded validation of exactly this revision in ``runs``,
    which callers pass newest first (as run repositories list them)."""
    for run in runs:
        if (
            run.scene_id == scene_id
            and run.status == RunStatus.SUCCEEDED
            and run.assessed(
                manifest_artifact_id=manifest_artifact_id,
                manifest_checksum=manifest_checksum,
            )
        ):
            return run
    return None


def derive_scene_readiness(
    validation_run: SceneValidationRunRecord | None,
) -> SceneReadiness:
    """Readiness from the validation run of the revision being assessed;
    callers pass the result of :func:`latest_validation_for_revision`."""
    if validation_run is None or validation_run.status != RunStatus.SUCCEEDED:
        return SceneReadiness.UNKNOWN
    status = (validation_run.validation_status or "").lower()
    if validation_run.should_block_pipeline or status in _BLOCKING_VALIDATION_STATUSES:
        return SceneReadiness.BLOCKED
    if status == "warning":
        return SceneReadiness.WARNING
    if status == "ready":
        return SceneReadiness.READY
    return SceneReadiness.UNKNOWN


__all__ = [
    "SceneReadiness",
    "derive_scene_readiness",
    "latest_validation_for_revision",
]
