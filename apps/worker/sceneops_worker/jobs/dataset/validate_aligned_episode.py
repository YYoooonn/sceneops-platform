from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.derived_ids import derived_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    AlignedEpisodeValidator,
    aligned_episode_alignment_key,
)
from sceneops_core.jobs.schemas import (
    JobType,
    ValidateAlignedEpisodeJobParams,
    ValidateAlignedEpisodeJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_episodes.artifacts import canonical_document_bytes
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    find_registered_output,
    require_member_source_revision,
    resolve_and_verify_aligned_artifact,
)

_validator = AlignedEpisodeValidator()


class ValidateAlignedEpisodeJobHandler(
    JobHandler[ValidateAlignedEpisodeJobParams, ValidateAlignedEpisodeJobResult]
):
    """AlignedEpisodeArtifact -> structural ValidationReport (SceneOps V2
    Request 2.4).

    Self-contained against the aligned artifact only -- never reads the
    original EpisodeManifest. All temporal/structural validation logic
    lives in sceneops-core's pure AlignedEpisodeValidator; this handler is
    resolution, checksum verification, and persistence only. Standalone
    Job, composable with PROFILE_ALIGNED_EPISODE, not gated on it (Request
    2.4 §36).
    """

    @property
    def job_type(self) -> JobType:
        return JobType.VALIDATE_ALIGNED_EPISODE

    @property
    def params_model(self) -> type[ValidateAlignedEpisodeJobParams]:
        return ValidateAlignedEpisodeJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            # Pinned by the upstream align_episode task when run in a pipeline.
            **{
                key: inputs.refs[key]
                for key in (
                    "episode_id",
                    "aligned_artifact_id",
                    "aligned_artifact_checksum",
                )
                if inputs.refs.get(key) is not None
            },
        }

    async def run(
        self,
        request: JobHandlerRequest[ValidateAlignedEpisodeJobParams],
    ) -> ValidateAlignedEpisodeJobResult:
        params = request.params
        context = request.context
        job = request.job

        _record, artifact, _verified = await resolve_and_verify_aligned_artifact(
            context,
            episode_id=params.episode_id,
            aligned_artifact_id=params.aligned_artifact_id,
            expected_checksum=params.aligned_artifact_checksum,
        )
        # What a report says of an aligned artifact holds in every DatasetVersion
        # pinning its source revision, so the scope is a check and no part of the
        # report. A request naming no DatasetVersion asks about the artifact alone.
        if params.dataset_id is not None or params.dataset_version is not None:
            if params.dataset_id is None or params.dataset_version is None:
                raise ValueError(
                    "dataset_id and dataset_version name one DatasetVersion: "
                    "state both or neither"
                )
            await require_member_source_revision(
                context,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                episode_id=params.episode_id,
                artifact=artifact,
            )

        report = _validator.validate(artifact)

        source_hash = artifact.source_revision.source_manifest_sha256
        key = aligned_episode_alignment_key(artifact.aligned_episode)

        data = canonical_document_bytes(report)
        checksum = sha256_checksum(data)
        report_artifact_id = derived_artifact_id(
            prefix="alignedvalidation",
            logical_id=params.aligned_artifact_id,
            checksum=checksum,
        )
        existing = await find_registered_output(
            context,
            artifact_id=report_artifact_id,
            kind=ArtifactKind.ALIGNED_EPISODE_VALIDATION_REPORT,
            data=data,
        )
        if existing is not None:
            report_uri = existing.uri
        else:
            write_result = (
                await context.episode_artifact_store.write_aligned_episode_report(
                    episode_id=params.episode_id,
                    source_manifest_sha256=source_hash,
                    alignment_key=key,
                    report_kind="validation",
                    report=report,
                )
            )
            report_uri = write_result.uri
            await context.artifact_record_store.register(
                artifact_id=report_artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.ALIGNED_EPISODE_VALIDATION_REPORT,
                    uri=report_uri,
                    media_type="application/json",
                    checksum=checksum,
                    size_bytes=len(data),
                    metadata={
                        "episode_id": params.episode_id,
                        "aligned_artifact_id": params.aligned_artifact_id,
                        "valid": report.valid,
                        "issue_count": len(report.issues),
                    },
                ),
                owner_type=ArtifactOwnerType.EPISODE,
                owner_id=params.episode_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        await context.commit()

        return ValidateAlignedEpisodeJobResult(
            episode_id=params.episode_id,
            aligned_artifact_id=params.aligned_artifact_id,
            aligned_artifact_checksum=params.aligned_artifact_checksum,
            validation_semantics_version=report.validation_semantics_version,
            valid=report.valid,
            issue_count=len(report.issues),
            report_artifact_id=report_artifact_id,
            report_uri=report_uri,
        )
