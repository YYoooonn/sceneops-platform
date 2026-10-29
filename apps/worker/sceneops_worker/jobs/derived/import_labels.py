"""IMPORT_LABELS: labels -> one immutable, registered label set revision
(ADR-007 §33.2, ADR-015 §9).

Labels are post-acquisition data with their own lineage. The importer never
touches a Scene, an Episode, a RobotRun or a DatasetVersion. Its input is
exactly one of:

    document_uri   an adapter-produced label set document, read from the
                   raw-source input store and validated
    snapshot_id    a registered SourceSnapshot -> its content_id, once
                                              (execution provenance, MS-14)
                   -> the registered content's stored inventory
                   -> the native label importer of the content's format
                   -> StoredContentFiles     the tables that importer reads, each
                                             verified against the inventory
                   -> the label set, a function of the content bytes alone

Either way the result is canonicalized into a ``LabelSetManifest`` revision,
every anchored source must be registered, and the revision is published
write-once and registered. Re-importing the same content is a no-op that
returns the same revision; a different document is a different revision (its
checksum differs), never an overwrite. A native import through another
snapshot of the same content yields the same revision; its artifact record
keeps the Job that first registered it, and this Job's result names the
snapshot it read.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.derived_ids import label_set_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    ImportLabelsJobParams,
    ImportLabelsJobResult,
    JobType,
)
from sceneops_core.labels import LabelSetManifest, LabelSetRef, parse_label_set_document
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.provenance import PinnedContent
from sceneops_core.sources import SourceContentInventory
from sceneops_derived.nuscenes_labels import nuscenes_label_set
from sceneops_sources import ContentFileError, StoredContentFiles
from sceneops_sources.nuscenes import (
    NuScenesFormatError,
    nuscenes_annotation_read_paths,
    nuscenes_read_paths,
)
from sceneops_storage import ArtifactNotFoundError

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.sources.content import resolve_registered_content


class UnknownLabelSourceError(ValueError):
    """A label anchor names a RobotRun or SourceContent that is not
    registered."""


class LabelDocumentChecksumError(ValueError):
    """The label document bytes are not the pinned bytes."""


class NativeLabelImportError(RuntimeError):
    """The snapshot cannot be imported: it is not registered, its content's
    format has no native label importer, or its files cannot be provided."""


@dataclass(frozen=True)
class _NativeLabelImporter:
    # The content files the importer's readers read.
    read_paths: Callable[[SourceContentInventory], list[str]]
    label_set: Callable[..., LabelSetManifest]


def _nuscenes_read_paths(inventory: SourceContentInventory) -> list[str]:
    return [*nuscenes_read_paths(inventory), *nuscenes_annotation_read_paths(inventory)]


# One native label importer per source format whose content carries the
# source's own annotations, selected by the format the content was registered
# under (as the snapshot builders are).
NATIVE_LABEL_IMPORTERS: dict[str, _NativeLabelImporter] = {
    "nuscenes": _NativeLabelImporter(
        read_paths=_nuscenes_read_paths, label_set=nuscenes_label_set
    ),
}


@dataclass(frozen=True)
class _Imported:
    manifest: LabelSetManifest
    metadata: JsonDict
    snapshot_id: str | None = None
    content_id: str | None = None


class ImportLabelsJobHandler(JobHandler[ImportLabelsJobParams, ImportLabelsJobResult]):
    @property
    def job_type(self) -> JobType:
        return JobType.IMPORT_LABELS

    @property
    def params_model(self) -> type[ImportLabelsJobParams]:
        return ImportLabelsJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {**inputs.params}

    async def run(
        self, request: JobHandlerRequest[ImportLabelsJobParams]
    ) -> ImportLabelsJobResult:
        params = request.params
        context = request.context
        job = request.job

        imported = (
            await _from_document(context, params)
            if params.document_uri is not None
            else await _from_snapshot(context, params)
        )
        manifest = imported.manifest
        robot_run_ids, content_ids = await _require_registered_sources(
            context, manifest
        )

        data = manifest.to_canonical_bytes()
        checksum = sha256_checksum(data)
        uri = context.derived_store.label_set_uri(
            label_set_id=manifest.label_set_id, checksum=checksum
        )
        published = await context.derived_store.publish(uri=uri, data=data)
        artifact_id = label_set_artifact_id(
            label_set_id=manifest.label_set_id, checksum=checksum
        )
        _, registered = await context.artifact_record_store.register(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.LABEL_SET_MANIFEST,
                uri=uri,
                media_type="application/json",
                checksum=checksum,
                size_bytes=published.size_bytes,
                metadata={
                    "label_set_id": manifest.label_set_id,
                    "provenance_kind": manifest.provenance.kind.value,
                    "producer": manifest.provenance.producer,
                    "label_count": len(manifest.labels),
                    **imported.metadata,
                },
            ),
            owner_type=ArtifactOwnerType.LABEL_SET,
            owner_id=manifest.label_set_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )
        await context.commit()

        return ImportLabelsJobResult(
            label_set=LabelSetRef(
                label_set_id=manifest.label_set_id,
                manifest_artifact_id=artifact_id,
                manifest_checksum=checksum,
            ),
            manifest_uri=uri,
            label_count=len(manifest.labels),
            covered_anchor_count=len(manifest.coverage),
            robot_run_ids=robot_run_ids,
            content_ids=content_ids,
            provenance_kind=manifest.provenance.kind.value,
            snapshot_id=imported.snapshot_id,
            content_id=imported.content_id,
            unit_keys=params.unit_keys or [],
            created=published.created or registered,
        )


async def _from_document(
    context: WorkerContext, params: ImportLabelsJobParams
) -> _Imported:
    assert params.document_uri is not None
    # A label document is an external input, like a raw source: it is read
    # from the raw-source store, never from the platform's artifact roots.
    try:
        document = await context.input_store.read_bytes(params.document_uri)
    except (ArtifactNotFoundError, FileNotFoundError) as exc:
        raise ValueError(f"label document not found: {params.document_uri}") from exc
    document_checksum = sha256_checksum(document)
    if params.expected_checksum is not None and (
        params.expected_checksum != document_checksum
    ):
        raise LabelDocumentChecksumError(
            f"label document {params.document_uri} hashes to {document_checksum}, "
            f"pinned {params.expected_checksum}"
        )
    return _Imported(
        manifest=parse_label_set_document(document),
        metadata={
            "source_document_uri": params.document_uri,
            "source_document_checksum": document_checksum,
        },
    )


async def _from_snapshot(
    context: WorkerContext, params: ImportLabelsJobParams
) -> _Imported:
    assert params.snapshot_id is not None and params.label_set_id is not None
    snapshot = await context.source_store.get_snapshot(params.snapshot_id)
    if snapshot is None:
        raise NativeLabelImportError(
            f"SourceSnapshot is not registered: {params.snapshot_id}"
        )
    content = await resolve_registered_content(context, snapshot.content_id)
    importer = NATIVE_LABEL_IMPORTERS.get(content.record.format)
    if importer is None:
        raise NativeLabelImportError(
            f"SourceContent {content.record.content_id} is registered as "
            f"{content.record.format!r}, which has no native label importer"
        )
    pinned = PinnedContent(inventory=content.inventory)
    with tempfile.TemporaryDirectory(prefix="sceneops-content-") as workdir:
        try:
            files = await StoredContentFiles.materialize(
                store=context.artifact_store,
                inventory_uri=content.inventory_uri,
                content_checksum=content.record.content_checksum,
                workdir=Path(workdir),
                paths=importer.read_paths(content.inventory),
            )
        except (ContentFileError, NuScenesFormatError) as exc:
            raise NativeLabelImportError(str(exc)) from exc
        manifest = await asyncio.to_thread(
            importer.label_set,
            pinned=pinned,
            files=files,
            label_set_id=params.label_set_id,
            unit_keys=params.unit_keys,
        )
    # The record names the content the bytes came from, never the snapshot:
    # the artifact is shared by every snapshot of the content.
    return _Imported(
        manifest=manifest,
        metadata={"source_content_id": content.record.content_id},
        snapshot_id=snapshot.snapshot_id,
        content_id=content.record.content_id,
    )


async def _require_registered_sources(
    context: WorkerContext, manifest: LabelSetManifest
) -> tuple[list[str], list[str]]:
    """The RobotRuns and SourceContents the anchors name, each of which must
    be registered: a label set never anchors on an unknown source."""
    robot_run_ids = sorted(
        {a.source_id for a in manifest.coverage if a.source_kind == "recording"}
    )
    content_ids = sorted(
        {a.source_id for a in manifest.coverage if a.source_kind == "content"}
    )
    unknown = [
        f"robot_run {run_id}"
        for run_id in robot_run_ids
        if await context.robot_store.get_run(run_id) is None
    ] + [
        f"content {content_id}"
        for content_id in content_ids
        if await context.source_store.get_content(content_id) is None
    ]
    if unknown:
        raise UnknownLabelSourceError(
            f"label set {manifest.label_set_id!r} anchors on unregistered "
            f"sources: {unknown[:10]}"
        )
    return robot_run_ids, content_ids
