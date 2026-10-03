"""Readiness of the Scene revisions a derived workflow consumes.

A derived workflow pins the revisions it reads (through the dataset index
it was given). Readiness is the validation result for exactly those
revisions -- never a cached DatasetVersion flag and never a run for some
other revision (ADR-007 §13.4, §18.5).
"""

from __future__ import annotations

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes import (
    SceneReadiness,
    derive_scene_readiness,
    latest_validation_for_revision,
)

from sceneops_worker.core.context import WorkerContext


async def pinned_revision_readiness(
    context: WorkerContext, dataset_manifest: DatasetManifest
) -> dict[str, SceneReadiness]:
    readiness: dict[str, SceneReadiness] = {}
    for entry in dataset_manifest.scenes:
        runs = await context.runs.scene_runs.list(
            type=RunType.SCENE_VALIDATION,
            status=RunStatus.SUCCEEDED,
            scene_id=entry.scene_id,
            manifest_artifact_id=entry.manifest_artifact_id,
            limit=20,
        )
        run = latest_validation_for_revision(
            runs,
            scene_id=entry.scene_id,
            manifest_artifact_id=entry.manifest_artifact_id,
            manifest_checksum=entry.manifest_checksum,
        )
        readiness[entry.scene_id] = derive_scene_readiness(run)
    return readiness


async def require_no_blocked_scenes(
    context: WorkerContext, dataset_manifest: DatasetManifest
) -> None:
    """Refuse to run a derived workflow over a Scene revision whose
    validation blocked downstream use. Unvalidated revisions are allowed."""
    readiness = await pinned_revision_readiness(context, dataset_manifest)
    blocked = sorted(s for s, r in readiness.items() if r == SceneReadiness.BLOCKED)
    if blocked:
        raise ValueError(
            f"Scene validation blocked downstream use of {len(blocked)} scene "
            f"revision(s) in {dataset_manifest.dataset_id}:"
            f"{dataset_manifest.dataset_version}: {blocked[:10]}"
        )
