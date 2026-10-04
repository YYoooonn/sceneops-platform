from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord

from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.episodes import EpisodeRepository


class InconsistentEpisodeRevisionError(RuntimeError):
    """An EpisodeRecord's pinned manifest artifact is missing or disagrees
    with the record. Reported, never repaired."""


async def resolve_current_episode_manifest_source(
    *,
    episode_repository: EpisodeRepository,
    artifact_repository: ArtifactRepository,
    episode_id: str,
) -> ArtifactRecord | None:
    """The EPISODE_MANIFEST ArtifactRecord that is the current revision of a
    registered Episode: exactly the one its record names by
    ``manifest_artifact_id`` (ADR-007 §14.4), never "the latest artifact".

    Shared by API job creation (before an ALIGN_EPISODE execution key is
    computed) and the ALIGN_EPISODE worker handler, so both resolve the same
    revision. Returns None if the Episode is not registered. Does not read
    manifest bytes; verifying them stays the worker's job.
    """
    record = await episode_repository.get(episode_id)
    if record is None:
        return None
    artifact = await artifact_repository.get(record.manifest_artifact_id)
    if (
        artifact is None
        or artifact.kind != ArtifactKind.EPISODE_MANIFEST
        or artifact.checksum != record.manifest_checksum
    ):
        raise InconsistentEpisodeRevisionError(
            f"Episode {episode_id} points to manifest artifact "
            f"{record.manifest_artifact_id} ({record.manifest_checksum}), which is "
            "missing or does not match"
        )
    return artifact
