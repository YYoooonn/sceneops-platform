"""Readiness of the Scene revisions a derived workflow consumes.

A derived workflow pins the Scene revisions it reads (through the sample
views it was given). Readiness is the validation result for exactly those
revisions -- never a cached DatasetVersion flag and never a run for some
other revision (ADR-007 §13.4, §18.5).
"""

from __future__ import annotations

from collections.abc import Iterable

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.sample_views import SceneRevisionRef
from sceneops_core.scenes import (
    SceneReadiness,
    derive_scene_readiness,
    latest_validation_for_revision,
)

from sceneops_worker.core.context import WorkerContext


async def pinned_revision_readiness(
    context: WorkerContext, pins: Iterable[SceneRevisionRef]
) -> dict[str, SceneReadiness]:
    readiness: dict[str, SceneReadiness] = {}
    for pin in pins:
        runs = await context.runs.scene_runs.list(
            type=RunType.SCENE_VALIDATION,
            status=RunStatus.SUCCEEDED,
            scene_id=pin.scene_id,
            manifest_artifact_id=pin.manifest_artifact_id,
            limit=20,
        )
        run = latest_validation_for_revision(
            runs,
            scene_id=pin.scene_id,
            manifest_artifact_id=pin.manifest_artifact_id,
            manifest_checksum=pin.manifest_checksum,
        )
        readiness[pin.scene_id] = derive_scene_readiness(run)
    return readiness


async def require_no_blocked_scenes(
    context: WorkerContext, pins: Iterable[SceneRevisionRef]
) -> None:
    """Refuse to run a derived workflow over a Scene revision whose
    validation blocked downstream use. Unvalidated revisions are allowed."""
    readiness = await pinned_revision_readiness(context, list(pins))
    blocked = sorted(s for s, r in readiness.items() if r == SceneReadiness.BLOCKED)
    if blocked:
        raise ValueError(
            f"Scene validation blocked downstream use of {len(blocked)} scene "
            f"revision(s): {blocked[:10]}"
        )
