"""Resolve a registered Episode revision to its verified manifest.

Every consumer of canonical Episode content goes through here: a revision
(a pin, or the one a DatasetVersion member pins) -> ``manifest_artifact_id``
-> ArtifactRecord -> bytes verified against the pinned checksum -> strict
canonical parse. An Episode unit has no current revision, so a consumer
always names the revision it reads; it never resolves "the latest
manifest" by location, creation time or registration order (ADR-015 §6.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeRevisionRecord
from sceneops_core.provenance import RevisionPin

from sceneops_worker.core.context import WorkerContext


class EpisodeNotRegisteredError(LookupError):
    pass


class InconsistentEpisodeStateError(RuntimeError):
    """A revision's manifest artifact is missing or disagrees with the
    revision. Reported, never repaired."""


@dataclass(frozen=True)
class ResolvedEpisode:
    record: EpisodeRevisionRecord
    manifest_artifact: ArtifactRecord
    manifest: EpisodeManifest


async def resolve_episode_revisions(
    context: WorkerContext, pins: Sequence[RevisionPin]
) -> list[ResolvedEpisode]:
    """The registered revision of every pin, in pin order; see
    ``sceneops_worker.scenes.resolver.resolve_scene_revisions``."""
    records = await context.episode_store.get_revisions(
        [(p.unit_id, p.manifest_checksum) for p in pins]
    )
    by_pin = {r.pin(): r for r in records}
    unregistered = [p for p in pins if p not in by_pin]
    if unregistered:
        raise EpisodeNotRegisteredError(
            "pins name no registered Episode revision: "
            + ", ".join(f"{p.unit_id}@{p.manifest_checksum}" for p in unregistered)
        )
    return [await resolve_registered_episode(context, by_pin[p]) for p in pins]


async def resolve_registered_episode(
    context: WorkerContext, record: EpisodeRevisionRecord
) -> ResolvedEpisode:
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
    "resolve_episode_revisions",
    "resolve_registered_episode",
]
