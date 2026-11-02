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
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    load_aligned_episode_artifact,
)

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.stores.artifacts import ArtifactRecordConflictError


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


class AlignedArtifactNotEligibleError(Exception):
    """The aligned artifact was computed from a revision the invoking
    DatasetVersion does not pin."""


class RegisteredArtifactIntegrityError(Exception):
    """A registered aligned artifact or report has lost its bytes, or they
    differ from what its record pins."""


async def require_member_source_revision(
    context: WorkerContext,
    *,
    dataset_id: str,
    dataset_version: str,
    episode_id: str,
    artifact: AlignedEpisodeArtifact,
) -> None:
    """An aligned artifact belongs to its source revision, not to a
    DatasetVersion, so a DatasetVersion may use exactly the artifacts computed
    from the revisions it pins. The artifact's own bytes name that revision; the
    ArtifactRecord's owner says only which Episode it is of, and any Job that
    can read the record could otherwise use it from any DatasetVersion."""
    scope = f"{dataset_id}:{dataset_version}"
    source = artifact.source_revision
    if source.episode_id != episode_id:
        raise AlignedArtifactNotEligibleError(
            f"aligned artifact of episode {source.episode_id!r} was requested as "
            f"episode {episode_id!r}"
        )
    member = await context.episode_store.get_member_revision(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        episode_id=episode_id,
    )
    if member is None:
        raise AlignedArtifactNotEligibleError(
            f"Episode {episode_id!r} is not a member of {scope}"
        )
    if (
        member.manifest_artifact_id != source.source_artifact_id
        or member.manifest_checksum.removeprefix("sha256:")
        != source.source_manifest_sha256
    ):
        raise AlignedArtifactNotEligibleError(
            f"the aligned artifact of episode {episode_id!r} was computed from "
            f"revision {source.source_artifact_id!r}, and {scope} pins "
            f"{member.manifest_artifact_id!r}"
        )


async def find_registered_output(
    context: WorkerContext,
    *,
    artifact_id: str,
    kind: ArtifactKind,
    data: bytes,
) -> ArtifactRecord | None:
    """The record of a content-derived id, when these bytes are already
    published: its object is what it pins, so nothing has to be written. Checked
    before any write, so a record that cannot be reused leaves no object behind.
    The location is the record's own, whichever location its writer used.

    ``None`` means nothing is registered yet. Two writers can both see that and
    both publish: they hold equal bytes for one key and one id, and converge
    (``write_once``, ``ArtifactRecordStore.register``)."""
    record = await context.artifact_record_store.get(artifact_id)
    if record is None:
        return None
    checksum = sha256_checksum(data)
    if (
        record.kind != kind.value
        or record.checksum != checksum
        or record.size_bytes != len(data)
    ):
        raise ArtifactRecordConflictError(
            f"artifact {artifact_id} is already registered as {record.kind} "
            f"{record.uri} {record.checksum}; refusing to reuse it for "
            f"{kind.value} {checksum}"
        )
    stored = await context.episode_artifact_store.read_aligned_episode_bytes(record.uri)
    if stored is None:
        raise RegisteredArtifactIntegrityError(
            f"artifact {artifact_id} is registered at {record.uri} and its bytes "
            "are missing"
        )
    if stored != data:
        raise RegisteredArtifactIntegrityError(
            f"artifact {artifact_id} at {record.uri} does not hold the bytes its "
            f"record pins ({checksum})"
        )
    return record


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

    artifact = load_aligned_episode_artifact(raw_bytes)
    return record, artifact, checksum_verified
