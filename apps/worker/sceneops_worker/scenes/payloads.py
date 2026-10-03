"""Resolve canonical payload references to their ArtifactRecords.

A ``PayloadRef`` names a payload by artifact id and pins its integrity; the
ArtifactRecord owns where the bytes live. A reference resolves only if its
ArtifactRecord exists, is an ``OBSERVATION_PAYLOAD``, and records exactly
the same checksum, size and media type.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord, PayloadRef

from sceneops_worker.stores.artifacts import ArtifactRecordStore


class PayloadIntegrityError(RuntimeError):
    """A payload reference has no matching ArtifactRecord."""


def payload_mismatch(ref: PayloadRef, record: ArtifactRecord | None) -> str | None:
    """Why ``record`` does not back ``ref``, or None if it does."""
    if record is None:
        return f"payload artifact {ref.artifact_id} is not registered"
    if record.kind != ArtifactKind.OBSERVATION_PAYLOAD:
        return f"payload artifact {ref.artifact_id} is a {record.kind!r}"
    for field in ("checksum", "size_bytes", "media_type"):
        if getattr(record, field) != getattr(ref, field):
            return (
                f"payload artifact {ref.artifact_id} {field} "
                f"{getattr(record, field)!r} != referenced {getattr(ref, field)!r}"
            )
    return None


async def resolve_payload_artifacts(
    artifact_record_store: ArtifactRecordStore, refs: Iterable[PayloadRef]
) -> Mapping[str, ArtifactRecord]:
    """ArtifactRecords for every reference, keyed by artifact id; raises if
    any reference does not resolve exactly."""
    refs = list(refs)
    records = await artifact_record_store.get_many(
        sorted({r.artifact_id for r in refs})
    )
    problems = sorted(
        {
            problem
            for ref in refs
            if (problem := payload_mismatch(ref, records.get(ref.artifact_id)))
        }
    )
    if problems:
        raise PayloadIntegrityError(
            f"{len(problems)} payload reference(s) do not resolve: {problems[:5]}"
        )
    return records


class ArtifactPayloadLocator:
    """Where verified payload bytes live, for workflows that read them."""

    def __init__(self, artifact_record_store: ArtifactRecordStore) -> None:
        self._store = artifact_record_store

    async def uris(self, refs: Iterable[PayloadRef]) -> dict[str, str]:
        records = await resolve_payload_artifacts(self._store, refs)
        return {artifact_id: record.uri for artifact_id, record in records.items()}


__all__ = [
    "ArtifactPayloadLocator",
    "PayloadIntegrityError",
    "payload_mismatch",
    "resolve_payload_artifacts",
]
