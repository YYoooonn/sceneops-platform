"""Resolve a registered Scene to the exact manifest revision it points to.

Every consumer of canonical Scene content goes through here: SceneRecord ->
``manifest_artifact_id`` -> ArtifactRecord -> bytes verified against the
pinned checksum -> strict canonical parse. A consumer never resolves "the
latest manifest" by location (ADR-007 §14.4, I-2).
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.scenes.schemas import SceneManifest, SceneRecord

from sceneops_worker.core.context import WorkerContext


class SceneNotRegisteredError(LookupError):
    pass


class InconsistentSceneStateError(RuntimeError):
    """A SceneRecord's pinned manifest artifact is missing or disagrees with
    the record. Reported, never repaired."""


@dataclass(frozen=True)
class ResolvedScene:
    record: SceneRecord
    manifest_artifact: ArtifactRecord
    manifest: SceneManifest


async def resolve_registered_scene(
    context: WorkerContext, scene: str | SceneRecord
) -> ResolvedScene:
    record = (
        scene
        if isinstance(scene, SceneRecord)
        else await context.scene_store.get(scene)
    )
    if record is None:
        raise SceneNotRegisteredError(f"Scene is not registered: {scene}")

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


# Upper bound of SceneRecords one derived workflow lists at once; a
# DatasetVersion with more members fails loudly instead of being processed
# partially.
MAX_LISTED_SCENES = 10_000


async def list_dataset_version_scenes(
    context: WorkerContext, *, dataset_id: str, dataset_version: str
) -> list[SceneRecord]:
    scenes = await context.scene_store.list(
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
]
