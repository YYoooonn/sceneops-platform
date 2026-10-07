"""Publication and registration of one immutable derived artifact revision.

A derived artifact is identified by its content: its bytes are written once at
a checksum-qualified key (``{stem}-{hex}{suffix}``) and its ArtifactRecord id
is ``derived_artifact_id(prefix, logical_id, checksum)``. So

* the record's checksum is the checksum of the bytes at its URI, and those
  bytes cannot change without a different key and a different record;
* a retry that reproduces the same bytes converges on the same object and the
  same record, whichever Job runs it and however many run concurrently
  (``ArtifactRecordStore.register`` inserts atomically and compares content);
* different bytes for the same ``logical_id`` are a new revision beside the
  old one, never a replacement.

``logical_id`` names what the artifact is a revision *of* (a run, a Scene, a
table of a DatasetVersion). The producing Job, run and PipelineRun are
recorded on the ArtifactRecord of the revision's first registration; they are
not part of its identity.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.checksums import checksum_hex, sha256_checksum
from sceneops_core.common.derived_ids import derived_artifact_id
from sceneops_core.common.schemas import JsonDict

from sceneops_worker.core.context import WorkerContext


@dataclass(frozen=True)
class RegisteredArtifact:
    artifact_id: str
    uri: str
    checksum: str
    size_bytes: int


async def register_published(
    context: WorkerContext,
    *,
    kind: ArtifactKind,
    prefix: str,
    logical_id: str,
    uri: str,
    checksum: str,
    size_bytes: int,
    media_type: str,
    metadata: JsonDict | None = None,
    **owner: str | None,
) -> RegisteredArtifact:
    """Register bytes that were already written once at ``uri`` (checksum and
    size are those of exactly those bytes) under their content-derived id.

    ``owner`` is the ArtifactRecord linkage (``owner_type``, ``owner_id``,
    ``dataset_id``, ``run_id``, ``job_id``, ...)."""
    artifact_id = derived_artifact_id(
        prefix=prefix, logical_id=logical_id, checksum=checksum
    )
    await context.artifact_record_store.register(
        artifact_id=artifact_id,
        ref=ArtifactRef(
            kind=kind,
            uri=uri,
            media_type=media_type,
            checksum=checksum,
            size_bytes=size_bytes,
            metadata=metadata or {},
        ),
        **owner,
    )
    return RegisteredArtifact(
        artifact_id=artifact_id, uri=uri, checksum=checksum, size_bytes=size_bytes
    )


async def publish_registered(
    context: WorkerContext,
    *,
    kind: ArtifactKind,
    prefix: str,
    logical_id: str,
    directory: str,
    stem: str,
    data: bytes,
    media_type: str,
    suffix: str = ".json",
    metadata: JsonDict | None = None,
    **owner: str | None,
) -> RegisteredArtifact:
    """Write ``data`` once at ``{directory}/{stem}-{hex}{suffix}`` and
    register its record."""
    checksum = sha256_checksum(data)
    uri = context.artifact_store.join_uri(
        directory, f"{stem}-{checksum_hex(checksum)}{suffix}"
    )
    written = await context.derived_store.publish(uri=uri, data=data)
    return await register_published(
        context,
        kind=kind,
        prefix=prefix,
        logical_id=logical_id,
        uri=uri,
        checksum=checksum,
        size_bytes=written.size_bytes,
        media_type=media_type,
        metadata=metadata,
        **owner,
    )


__all__ = ["RegisteredArtifact", "publish_registered", "register_published"]
