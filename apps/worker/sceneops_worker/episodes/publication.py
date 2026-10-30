"""Publication of built EpisodeManifests, shared by every Episode build
(recording and snapshot): write-once manifest bytes at the revision's
checksum-qualified key, then the EPISODE_MANIFEST ArtifactRecord under its
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
from sceneops_core.episodes.schemas import episode_id_for
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_episodes.plan import EpisodeBuildPlan

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.recordings.artifact_records import require_same_artifact


async def publish_episode_manifests(
    context: WorkerContext,
    plan: EpisodeBuildPlan,
    *,
    job_id: str,
    pipeline_run_id: str | None,
) -> tuple[list[str], int]:
    """The manifest artifact ids of ``plan`` in plan order, and how many of
    their records this call created."""
    artifact_ids: list[str] = []
    created = 0
    for episode in plan.episodes:
        episode_id = episode_id_for(episode.manifest.lineage.source)
        published = await context.episode_artifact_store.publish_canonical_manifest(
            episode_id=episode_id,
            manifest=episode.manifest,
        )
        artifact_id = manifest_artifact_id_for(
            unit_id=episode_id, manifest_checksum=published.checksum
        )
        ref = ArtifactRef(
            kind=ArtifactKind.EPISODE_MANIFEST,
            uri=published.uri,
            media_type="application/json",
            checksum=published.checksum,
            size_bytes=published.size_bytes,
        )
        record, registered = await context.artifact_record_store.create_if_absent(
            artifact_id=artifact_id,
            ref=ref,
            owner_type=ArtifactOwnerType.EPISODE,
            owner_id=episode_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
        )
        if registered:
            created += 1
        else:
            require_same_artifact(record, ref)
        artifact_ids.append(artifact_id)
    return artifact_ids, created


__all__ = ["publish_episode_manifests"]
