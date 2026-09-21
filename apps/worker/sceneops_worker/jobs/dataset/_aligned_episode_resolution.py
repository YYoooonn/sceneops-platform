"""Shared resolve/verify/parse plumbing for VALIDATE_ALIGNED_EPISODE and
PROFILE_ALIGNED_EPISODE (SceneOps V2 Request 2.4 §35-37).

Both jobs need the exact same sequence -- resolve the pinned
ALIGNED_EPISODE_MANIFEST ArtifactRecord, read its exact bytes, verify a
checksum before trusting them, then parse -- so it lives once here rather
than duplicated across two handler files. Neither job's own analysis logic
(validation/profiling) lives here; this module is I/O and identity
resolution only.
"""

from __future__ import annotations

import hashlib

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.alignment import AlignedEpisodeArtifact

from sceneops_worker.core.context import WorkerContext


class AlignedArtifactNotFoundError(Exception):
    """No ALIGNED_EPISODE_MANIFEST ArtifactRecord (or its bytes) could be
    resolved for the pinned aligned_artifact_id."""


class AlignedArtifactChecksumMismatchError(Exception):
    """The bytes actually read do not match the expected checksum (either
    the caller's pinned aligned_artifact_checksum, or the resolved
    ArtifactRecord's own checksum). Mirrors ALIGN_EPISODE's source-checksum
    protection (Request 2.3 §8) for the aligned artifact itself (Request
    2.4 §37/§50): a forced ALIGN_EPISODE re-execution can still overwrite
    the same deterministic aligned-artifact URI between this job's creation
    and its execution."""


async def resolve_and_verify_aligned_artifact(
    context: WorkerContext,
    *,
    episode_id: str,
    aligned_artifact_id: str,
    expected_checksum: str | None,
) -> tuple[ArtifactRecord, AlignedEpisodeArtifact, bool]:
    """Returns (artifact_record, parsed_artifact, checksum_verified).

    Always pinned by aligned_artifact_id (Request 2.4 §34 -- one Episode can
    have many aligned artifacts, so there is no "latest" to resolve
    unambiguously). expected_checksum, when provided, is the authoritative
    check; when absent, falls back to the resolved ArtifactRecord's own
    checksum (populated unconditionally by Request 2.3's write path, so this
    fallback is defensive symmetry with ALIGN_EPISODE's source-checksum
    verification, not an expected legacy case).
    """
    record = await context.artifact_record_store.get(aligned_artifact_id)
    if record is None or record.kind != ArtifactKind.ALIGNED_EPISODE_MANIFEST.value:
        raise AlignedArtifactNotFoundError(
            f"aligned_artifact_id {aligned_artifact_id!r} is not a valid "
            "ALIGNED_EPISODE_MANIFEST ArtifactRecord"
        )
    if (
        record.owner_type != ArtifactOwnerType.EPISODE.value
        or record.owner_id != episode_id
    ):
        raise AlignedArtifactNotFoundError(
            f"aligned_artifact_id {aligned_artifact_id!r} does not belong to "
            f"episode {episode_id!r}"
        )

    raw_bytes = await context.episode_artifact_store.read_aligned_episode_bytes(
        record.uri
    )
    if raw_bytes is None:
        raise AlignedArtifactNotFoundError(
            f"Aligned episode artifact bytes not found at {record.uri!r} "
            f"(artifact_id={aligned_artifact_id!r})"
        )

    computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    checksum_verified = False

    if expected_checksum is not None:
        expected = expected_checksum.removeprefix("sha256:")
        if expected != computed_sha256:
            raise AlignedArtifactChecksumMismatchError(
                f"Aligned artifact checksum mismatch for episode "
                f"{episode_id!r}: pinned aligned_artifact_checksum="
                f"{expected_checksum!r}, actual bytes hash to "
                f"{computed_sha256!r}"
            )
        checksum_verified = True
    elif record.checksum is not None:
        expected = record.checksum.removeprefix("sha256:")
        if expected != computed_sha256:
            raise AlignedArtifactChecksumMismatchError(
                f"Aligned artifact checksum mismatch for episode "
                f"{episode_id!r}: ArtifactRecord {aligned_artifact_id!r} "
                f"declares checksum={record.checksum!r}, actual bytes hash to "
                f"sha256:{computed_sha256}"
            )
        checksum_verified = True

    artifact = AlignedEpisodeArtifact.model_validate_json(raw_bytes)
    return record, artifact, checksum_verified
