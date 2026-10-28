"""Publication of built SceneManifests, shared by every Scene build
(recording and snapshot): write-once manifest bytes at the revision's
checksum-qualified key, then the SCENE_MANIFEST ArtifactRecord under its
deterministic id, carrying the building Job and PipelineRun as producer
lineage (ADR-015 §4: the Job that first produced a revision).

A rebuild of identical bytes reuses both: the object is compared, and an
existing record must agree on location and integrity, so it keeps the Job
that first produced it. Anything that differs fails the build.
"""

from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_scenes.plan import SceneBuildPlan

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.recordings.artifact_records import require_same_artifact


async def publish_scene_manifests(
    context: WorkerContext,
    plan: SceneBuildPlan,
    *,
    job_id: str,
    pipeline_run_id: str | None,
) -> tuple[list[str], int]:
    """The manifest artifact ids of ``plan`` in plan order, and how many of
    their records this call created."""
    artifact_ids: list[str] = []
    created = 0
    for scene in plan.scenes:
        scene_id = scene_id_for(scene.manifest.lineage.source)
        published = await context.scene_artifact_store.publish_canonical_manifest(
            scene_id=scene_id,
            manifest=scene.manifest,
        )
        artifact_id = manifest_artifact_id_for(
            unit_id=scene_id, manifest_checksum=published.checksum
        )
        ref = ArtifactRef(
            kind=ArtifactKind.SCENE_MANIFEST,
            uri=published.uri,
            media_type="application/json",
            checksum=published.checksum,
            size_bytes=published.size_bytes,
        )
        record, registered = await context.artifact_record_store.create_if_absent(
            artifact_id=artifact_id,
            ref=ref,
            owner_type=ArtifactOwnerType.SCENE,
            owner_id=scene_id,
            scene_id=scene_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
        )
        if registered:
            created += 1
        else:
            require_same_artifact(record, ref)
        artifact_ids.append(artifact_id)
    return artifact_ids, created


__all__ = ["publish_scene_manifests"]
