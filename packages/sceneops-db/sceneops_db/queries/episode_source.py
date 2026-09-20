from __future__ import annotations

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)

from sceneops_db.repositories.artifacts import ArtifactRepository


async def resolve_current_episode_manifest_source(
    repository: ArtifactRepository,
    *,
    episode_id: str,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
) -> ArtifactRecord | None:
    """The single "which EPISODE_MANIFEST ArtifactRecord is current for this
    episode" rule (SceneOps V2 Request 2.3A §5/§13).

    Shared by API job-creation (SceneOps V2 Request 2.3A, before the
    execution key is computed) and the ALIGN_EPISODE worker handler's
    unpinned path (Request 2.3), so the two layers can never drift onto
    different selection rules. "Current" = latest by created_at -- the same
    "latest wins" convention already used everywhere else in this platform
    (run records, validation/profile results), operating against the same
    ArtifactRepository Protocol both apps' artifact stores already wrap.

    Returns the full ArtifactRecord (artifact_id, uri, checksum, ...) --
    callers extract what they need. Does not read or verify manifest bytes;
    that integrity check stays the worker's exclusive responsibility
    (Request 2.3A §14).
    """
    records = await repository.list(
        kind=ArtifactKind.EPISODE_MANIFEST,
        owner_type=ArtifactOwnerType.EPISODE,
        owner_id=episode_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        limit=1,
    )
    return records[0] if records else None
