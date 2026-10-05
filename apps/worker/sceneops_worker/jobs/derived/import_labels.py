"""IMPORT_LABELS: a label set document -> one immutable, registered label
set revision (ADR-007 §33.2).

Labels are post-acquisition data with their own lineage. The importer never
touches a Scene, an Episode or a RobotRun: it validates an adapter-produced
document, canonicalizes it into a ``LabelSetManifest`` revision, publishes
that revision write-once and registers it. Re-importing the same content is
a no-op that returns the same revision; a different document is a different
revision (its checksum differs), never an overwrite.
"""

from __future__ import annotations

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
from sceneops_core.labels import LabelSetRef, parse_label_set_document
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_storage import ArtifactNotFoundError

from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class UnknownRobotRunError(ValueError):
    """A label anchor names a RobotRun that is not registered."""


class LabelDocumentChecksumError(ValueError):
    """The label document bytes are not the pinned bytes."""


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

        # A label document is an external input, like a raw source: it is read
        # from the raw-source store, never from the platform's artifact roots.
        try:
            document = await context.input_store.read_bytes(params.document_uri)
        except (ArtifactNotFoundError, FileNotFoundError) as exc:
            raise ValueError(
                f"label document not found: {params.document_uri}"
            ) from exc
        document_checksum = sha256_checksum(document)
        if params.expected_checksum is not None and (
            params.expected_checksum != document_checksum
        ):
            raise LabelDocumentChecksumError(
                f"label document {params.document_uri} hashes to {document_checksum}, "
                f"pinned {params.expected_checksum}"
            )

        manifest = parse_label_set_document(document)

        robot_run_ids = sorted({a.robot_run_id for a in manifest.coverage})
        unknown = [
            run_id
            for run_id in robot_run_ids
            if await context.robot_store.get_run(run_id) is None
        ]
        if unknown:
            raise UnknownRobotRunError(
                f"label set {manifest.label_set_id!r} anchors on unregistered "
                f"RobotRuns: {unknown[:10]}"
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
                    "source_document_uri": params.document_uri,
                    "source_document_checksum": document_checksum,
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
            provenance_kind=manifest.provenance.kind.value,
            created=published.created or registered,
        )
