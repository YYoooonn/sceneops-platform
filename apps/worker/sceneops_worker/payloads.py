"""Where verified payload bytes live, for workflows that read them
(ADR-015 §6.5, MS-10).

``PayloadLocator`` is the one place downstream of canonical manifests that
dispatches on the payload reference kind:

    ArtifactPayloadRef  its OBSERVATION_PAYLOAD ArtifactRecord, which must
                        record exactly the referenced checksum, size and media
                        type -> the record's URI
    SourceFileRef       its registered content's stored inventory, whose entry
                        for the path must pin exactly the referenced sha256
                        and size -> the preserved file's URI, derived from the
                        inventory ArtifactRecord (D4)

A reference that does not resolve exactly fails; nothing is looked up by
location. The bytes behind a URI are the reader's to verify against the
reference's checksum.
"""

from __future__ import annotations

from collections.abc import Iterable

from sceneops_core.artifacts.schemas import (
    ArtifactPayloadRef,
    PayloadRef,
    SourceFileRef,
)
from sceneops_sources import content_file_uri

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.recordings.payload_refs import (
    PayloadIntegrityError,
    resolve_payload_artifacts,
)
from sceneops_worker.sources.content import (
    RegisteredContentError,
    resolve_registered_content,
    source_file_ref_mismatch,
)


class PayloadLocator:
    def __init__(self, context: WorkerContext) -> None:
        self._context = context

    async def uris(self, refs: Iterable[PayloadRef]) -> dict[PayloadRef, str]:
        refs = list(refs)
        artifacts = [r for r in refs if isinstance(r, ArtifactPayloadRef)]
        files = [r for r in refs if isinstance(r, SourceFileRef)]
        records = await resolve_payload_artifacts(
            self._context.artifact_record_store, artifacts
        )
        found: dict[PayloadRef, str] = {
            ref: records[ref.artifact_id].uri for ref in artifacts
        }
        for content_id in sorted({r.content_id for r in files}):
            try:
                content = await resolve_registered_content(self._context, content_id)
            except RegisteredContentError as exc:
                raise PayloadIntegrityError(str(exc)) from exc
            entries = {entry.path: entry for entry in content.inventory.files}
            for ref in (r for r in files if r.content_id == content_id):
                problem = source_file_ref_mismatch(ref, content_id, entries)
                if problem is not None:
                    raise PayloadIntegrityError(
                        f"source file reference does not resolve: {problem}"
                    )
                found[ref] = content_file_uri(content.inventory_uri, ref.path)
        return found


__all__ = ["PayloadLocator"]
