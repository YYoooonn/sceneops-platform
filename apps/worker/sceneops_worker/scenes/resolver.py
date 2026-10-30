"""Resolve a registered Scene revision to its verified manifest.

Every consumer of canonical Scene content goes through here: a revision
(a pin, or the one a DatasetVersion member pins) -> ``manifest_artifact_id``
-> ArtifactRecord -> bytes verified against the pinned checksum -> strict
canonical parse. A Scene unit has no current revision, so a consumer always
names the revision it reads; it never resolves "the latest manifest" by
location or by registration order (ADR-015 §6.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.provenance import RevisionPin
from sceneops_core.scenes.schemas import SceneManifest, SceneRevisionRecord

from sceneops_worker.core.context import WorkerContext


class SceneNotRegisteredError(LookupError):
    pass


class InconsistentSceneStateError(RuntimeError):
    """A revision's manifest artifact is missing or disagrees with the
    revision. Reported, never repaired."""


@dataclass(frozen=True)
class ResolvedScene:
    record: SceneRevisionRecord
    manifest_artifact: ArtifactRecord
    manifest: SceneManifest


async def resolve_scene_revisions(
    context: WorkerContext, pins: Sequence[RevisionPin]
) -> list[ResolvedScene]:
    """The registered revision of every pin, in pin order. A pin must match
    its revision row exactly (unit, manifest checksum and manifest artifact
    id); a pin that names no registered revision fails before any manifest
    is read."""
    records = await context.scene_store.get_revisions(
        [(p.unit_id, p.manifest_checksum) for p in pins]
    )
    by_pin = {r.pin(): r for r in records}
    unregistered = [p for p in pins if p not in by_pin]
    if unregistered:
        raise SceneNotRegisteredError(
            "pins name no registered Scene revision: "
            + ", ".join(f"{p.unit_id}@{p.manifest_checksum}" for p in unregistered)
        )
    return [await resolve_registered_scene(context, by_pin[p]) for p in pins]


async def resolve_registered_scene(
    context: WorkerContext, record: SceneRevisionRecord
) -> ResolvedScene:
    artifact = await context.artifact_record_store.get(record.manifest_artifact_id)
    if artifact is None:
        raise InconsistentSceneStateError(
            f"Scene {record.scene_id} references missing manifest artifact "
            f"{record.manifest_artifact_id}"
        )
    if (
        artifact.kind != ArtifactKind.SCENE_MANIFEST
        or artifact.checksum != record.manifest_checksum
    ):
        raise InconsistentSceneStateError(
            f"Scene {record.scene_id} manifest artifact {artifact.artifact_id} "
            f"(kind={artifact.kind}, checksum={artifact.checksum}) does not match "
            f"the record's pinned revision {record.manifest_checksum}"
        )

    manifest = await context.scene_artifact_store.read_pinned_manifest(
        uri=artifact.uri,
        checksum=record.manifest_checksum,
        size_bytes=artifact.size_bytes,
    )
    return ResolvedScene(record=record, manifest_artifact=artifact, manifest=manifest)


# Upper bound of Scene members one derived workflow lists at once; a
# DatasetVersion with more members fails loudly instead of being processed
# partially.
MAX_LISTED_SCENES = 10_000


async def list_dataset_version_scenes(
    context: WorkerContext, *, dataset_id: str, dataset_version: str
) -> list[SceneRevisionRecord]:
    """The Scene revisions a DatasetVersion pins, one per member unit."""
    scenes = await context.scene_store.list_member_revisions(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        limit=MAX_LISTED_SCENES + 1,
    )
    if len(scenes) > MAX_LISTED_SCENES:
        raise ValueError(
            f"{dataset_id}/{dataset_version} has more than {MAX_LISTED_SCENES} "
            "registered Scenes; derived workflows do not page yet"
        )
    return scenes


__all__ = [
    "InconsistentSceneStateError",
    "ResolvedScene",
    "SceneNotRegisteredError",
    "list_dataset_version_scenes",
    "resolve_registered_scene",
    "resolve_scene_revisions",
]
