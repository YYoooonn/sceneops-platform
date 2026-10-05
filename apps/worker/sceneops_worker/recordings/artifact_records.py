"""Deterministic, write-once ArtifactRecords of recording builders."""

from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.artifacts.schemas.refs import ArtifactRef


class ArtifactRecordConflictError(RuntimeError):
    """A deterministic artifact id is registered with different content."""


def require_same_artifact(existing: ArtifactRecord, ref: ArtifactRef) -> None:
    """An existing record of a deterministic id must describe exactly the
    bytes the build produced; it is reused, never updated."""
    mismatched = [
        name
        for name in ("kind", "uri", "checksum", "size_bytes", "media_type")
        if getattr(existing, name) != getattr(ref, name)
    ]
    if mismatched:
        raise ArtifactRecordConflictError(
            f"ArtifactRecord {existing.artifact_id} already exists with different "
            f"{mismatched}; deterministic artifact ids are write-once"
        )


__all__ = ["ArtifactRecordConflictError", "require_same_artifact"]
