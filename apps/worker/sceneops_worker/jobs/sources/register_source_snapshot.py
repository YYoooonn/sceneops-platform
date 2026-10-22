from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterSourceSnapshotJobParams,
    RegisterSourceSnapshotJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.sources import register_source_snapshot


class RegisterSourceSnapshotJobHandler(
    JobHandler[RegisterSourceSnapshotJobParams, RegisterSourceSnapshotJobResult]
):
    """REGISTER_SOURCE_SNAPSHOT(descriptor_uri): verifies a published
    SourceSnapshot, its content inventory and, for new content, every
    preserved file, then registers the content and snapshot records with
    their ArtifactRecords in one transaction (ADR-015 §5).

    A standalone job. Retries converge: an identical registration reports
    both records as ``existing`` and writes nothing; an inconsistent
    declaration fails.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_SOURCE_SNAPSHOT

    @property
    def params_model(self) -> type[RegisterSourceSnapshotJobParams]:
        return RegisterSourceSnapshotJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return dict(inputs.params)

    async def run(
        self,
        request: JobHandlerRequest[RegisterSourceSnapshotJobParams],
    ) -> RegisterSourceSnapshotJobResult:
        registration = await register_source_snapshot(
            context=request.context,
            descriptor_uri=request.params.descriptor_uri,
            job_id=request.job.job_id,
        )
        content = registration.content
        return RegisterSourceSnapshotJobResult(
            snapshot_id=registration.snapshot.snapshot_id,
            content_id=content.content_id,
            content=registration.content_outcome,
            snapshot=registration.snapshot_outcome,
            file_count=content.file_count,
            total_bytes=content.total_bytes,
        )
