"""Resolve a registered Episode to the exact manifest revision it points to.

Every consumer of canonical Episode content goes through here: EpisodeRecord
-> ``manifest_artifact_id`` -> ArtifactRecord -> bytes verified against the
pinned checksum -> strict canonical parse. A consumer never resolves "the
latest manifest" by location or creation time (ADR-007 §14.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeRecord

from sceneops_worker.core.context import WorkerContext


class EpisodeNotRegisteredError(LookupError):
    pass


class InconsistentEpisodeStateError(RuntimeError):
    """An EpisodeRecord's pinned manifest artifact is missing or disagrees
    with the record. Reported, never repaired."""


@dataclass(frozen=True)
class ResolvedEpisode:
    record: EpisodeRecord
    manifest_artifact: ArtifactRecord
    manifest: EpisodeManifest


async def resolve_registered_episode(
    context: WorkerContext, episode: str | EpisodeRecord
) -> ResolvedEpisode:
    record = (
        episode
        if isinstance(episode, EpisodeRecord)
        else await context.episode_store.get(episode)
    )
    if record is None:
        raise EpisodeNotRegisteredError(f"Episode is not registered: {episode}")
    artifact = await context.artifact_record_store.get(record.manifest_artifact_id)
    if artifact is None:
        raise InconsistentEpisodeStateError(
            f"Episode {record.episode_id} references missing manifest artifact "
            f"{record.manifest_artifact_id}"
        )
    if (
        artifact.kind != ArtifactKind.EPISODE_MANIFEST
        or artifact.checksum != record.manifest_checksum
    ):
        raise InconsistentEpisodeStateError(
            f"Episode {record.episode_id} manifest artifact {artifact.artifact_id} "
            f"(kind={artifact.kind}, checksum={artifact.checksum}) does not match "
            f"the record's pinned revision {record.manifest_checksum}"
        )
    manifest = await context.episode_artifact_store.read_pinned_manifest(
        uri=artifact.uri,
        checksum=record.manifest_checksum,
        size_bytes=artifact.size_bytes,
    )
    return ResolvedEpisode(record=record, manifest_artifact=artifact, manifest=manifest)


__all__ = [
    "EpisodeNotRegisteredError",
    "InconsistentEpisodeStateError",
    "ResolvedEpisode",
    "resolve_registered_episode",
]
