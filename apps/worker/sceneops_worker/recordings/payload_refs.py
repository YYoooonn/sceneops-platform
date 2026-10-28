"""Resolve canonical payload references to their ArtifactRecords.

An ``ArtifactPayloadRef`` names a payload by artifact id and pins its integrity; the
ArtifactRecord owns where the bytes live. A reference resolves only if its
ArtifactRecord exists, is an ``OBSERVATION_PAYLOAD``, and records exactly
the same checksum, size and media type. ``verify_payload_objects`` checks
the store itself: each object is present with its recorded size.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactPayloadRef,
    ArtifactRecord,
)

from sceneops_worker.stores.artifacts import ArtifactRecordStore


class PayloadIntegrityError(RuntimeError):
    """A payload reference has no matching ArtifactRecord."""


def payload_mismatch(
    ref: ArtifactPayloadRef, record: ArtifactRecord | None
) -> str | None:
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
    artifact_record_store: ArtifactRecordStore, refs: Iterable[ArtifactPayloadRef]
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


async def verify_payload_objects(
    artifact_store: ArtifactStore, records: Iterable[ArtifactRecord]
) -> None:
    """Every record's object is present in the store with its recorded size
    (ADR-015 §6.4). An ArtifactRecord proves the bytes were registered, not
    that they are still stored, so this asks the store itself: one listing
    per payload prefix, not one request per payload. Checksums are not
    re-hashed here; payload bytes were hashed on publication and are
    re-verified on every pinned read."""
    by_prefix: dict[str, list[ArtifactRecord]] = {}
    for record in records:
        prefix, _, name = record.uri.rpartition("/")
        if not prefix or not name:
            raise PayloadIntegrityError(
                f"payload artifact {record.artifact_id} has no object key: "
                f"{record.uri!r}"
            )
        by_prefix.setdefault(prefix, []).append(record)
    problems: list[str] = []
    for prefix, expected in sorted(by_prefix.items()):
        stored = {
            o.uri: o.size_bytes for o in await artifact_store.list_objects(prefix)
        }
        for record in expected:
            size = stored.get(record.uri)
            if size is None:
                problems.append(
                    f"payload artifact {record.artifact_id} has no object at "
                    f"{record.uri}"
                )
            elif size != record.size_bytes:
                problems.append(
                    f"payload artifact {record.artifact_id} object has {size} "
                    f"bytes, recorded {record.size_bytes}"
                )
    if problems:
        problems.sort()
        raise PayloadIntegrityError(
            f"{len(problems)} payload object(s) are not stored as recorded: "
            f"{problems[:5]}"
        )


__all__ = [
    "PayloadIntegrityError",
    "payload_mismatch",
    "resolve_payload_artifacts",
    "verify_payload_objects",
]
