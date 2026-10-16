from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord

from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.episodes import EpisodeRepository


class InconsistentEpisodeRevisionError(RuntimeError):
    """A revision's manifest artifact is missing or disagrees with the
    revision. Reported, never repaired."""


async def resolve_member_episode_manifest_source(
    *,
    episode_repository: EpisodeRepository,
    artifact_repository: ArtifactRepository,
    dataset_id: str,
    dataset_version: str,
    episode_id: str,
) -> ArtifactRecord | None:
    """The EPISODE_MANIFEST ArtifactRecord of the revision of ``episode_id``
    that the DatasetVersion pins, never "the latest artifact": an Episode
    has no current revision (ADR-015 §6.4).

    Shared by API job creation (before an ALIGN_EPISODE execution key is
    computed) and the Episode manifest endpoint, so both resolve the same
    revision. Returns None if the Episode is not a member of the
    DatasetVersion. Does not read manifest bytes; verifying them stays the
    reader's job.
    """
    revision = await episode_repository.get_member_revision(
        dataset_id=dataset_id, dataset_version=dataset_version, episode_id=episode_id
    )
    if revision is None:
        return None
    artifact = await artifact_repository.get(revision.manifest_artifact_id)
    if (
        artifact is None
        or artifact.kind != ArtifactKind.EPISODE_MANIFEST
        or artifact.checksum != revision.manifest_checksum
    ):
        raise InconsistentEpisodeRevisionError(
            f"Episode {episode_id} revision {revision.manifest_checksum} names "
            f"manifest artifact {revision.manifest_artifact_id}, which is missing "
            "or does not match"
        )
    return artifact
