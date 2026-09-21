"""Shared resolve/verify/parse plumbing for CURATE_EPISODES (SceneOps V2
Request 2.6 §3/§11).

Mirrors _aligned_episode_resolution.py's role one layer up: resolve the
pinned LEARNING_DATA_EXPORT_MANIFEST ArtifactRecord, read its exact bytes,
verify a checksum before trusting them, then parse -- so CURATE_EPISODES
never silently curates over newer/different bytes than the caller pinned
(the checksum-race protection required by Request 2.6 §15/§17).
"""

from __future__ import annotations

import hashlib

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.learning_export import LearningDataExportManifest

from sceneops_worker.core.context import WorkerContext


class LearningExportManifestNotFoundError(Exception):
    """No LEARNING_DATA_EXPORT_MANIFEST ArtifactRecord (or its bytes) could
    be resolved for the pinned manifest_artifact_id."""


class LearningExportManifestChecksumMismatchError(Exception):
    """The bytes actually read do not match the expected checksum (either
    the caller's pinned learning_data_export_manifest_checksum, or the
    resolved ArtifactRecord's own checksum). Mirrors
    AlignedArtifactChecksumMismatchError's race protection one layer up."""


async def resolve_and_verify_learning_export_manifest(
    context: WorkerContext,
    *,
    manifest_artifact_id: str,
    expected_checksum: str | None,
) -> tuple[ArtifactRecord, LearningDataExportManifest, bool]:
    """Returns (artifact_record, parsed_manifest, checksum_verified).

    Always pinned by manifest_artifact_id -- there is no "latest export" to
    resolve unambiguously, matching resolve_and_verify_aligned_artifact's
    reasoning for aligned artifacts (Request 2.4 §34) applied one layer up.
    expected_checksum, when provided, is the authoritative check; when
    absent, falls back to the resolved ArtifactRecord's own checksum.
    """
    record = await context.artifact_record_store.get(manifest_artifact_id)
    if (
        record is None
        or record.kind != ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST.value
    ):
        raise LearningExportManifestNotFoundError(
            f"manifest_artifact_id {manifest_artifact_id!r} is not a valid "
            "LEARNING_DATA_EXPORT_MANIFEST ArtifactRecord"
        )
    if record.owner_type != ArtifactOwnerType.DATASET_VERSION.value:
        raise LearningExportManifestNotFoundError(
            f"manifest_artifact_id {manifest_artifact_id!r} is not owned by "
            "a DatasetVersion"
        )

    raw_bytes = await context.analytics_writer.read_learning_export_manifest_bytes(
        record.uri
    )
    if raw_bytes is None:
        raise LearningExportManifestNotFoundError(
            f"Learning data export manifest bytes not found at {record.uri!r} "
            f"(artifact_id={manifest_artifact_id!r})"
        )

    computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    checksum_verified = False

    if expected_checksum is not None:
        expected = expected_checksum.removeprefix("sha256:")
        if expected != computed_sha256:
            raise LearningExportManifestChecksumMismatchError(
                "Learning data export manifest checksum mismatch for "
                f"manifest_artifact_id={manifest_artifact_id!r}: pinned "
                f"learning_data_export_manifest_checksum={expected_checksum!r}, "
                f"actual bytes hash to {computed_sha256!r}"
            )
        checksum_verified = True
    elif record.checksum is not None:
        expected = record.checksum.removeprefix("sha256:")
        if expected != computed_sha256:
            raise LearningExportManifestChecksumMismatchError(
                "Learning data export manifest checksum mismatch for "
                f"manifest_artifact_id={manifest_artifact_id!r}: ArtifactRecord "
                f"declares checksum={record.checksum!r}, actual bytes hash to "
                f"sha256:{computed_sha256}"
            )
        checksum_verified = True

    manifest = LearningDataExportManifest.model_validate_json(raw_bytes)
    return record, manifest, checksum_verified
