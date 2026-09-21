from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import AlignedEpisodeValidator, alignment_key
from sceneops_core.jobs.schemas import (
    JobType,
    ValidateAlignedEpisodeJobParams,
    ValidateAlignedEpisodeJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
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
        }

    async def run(
        self,
        request: JobHandlerRequest[ValidateAlignedEpisodeJobParams],
    ) -> ValidateAlignedEpisodeJobResult:
        params = request.params
        context = request.context
        job = request.job

        dataset_id = params.dataset_id or context.default_dataset_id
        dataset_version = params.dataset_version or context.default_dataset_version

        _record, artifact, _verified = await resolve_and_verify_aligned_artifact(
            context,
            episode_id=params.episode_id,
            aligned_artifact_id=params.aligned_artifact_id,
            expected_checksum=params.aligned_artifact_checksum,
        )

        report = _validator.validate(artifact)

        source_hash = artifact.source_revision.source_manifest_sha256
        key = alignment_key(
            artifact.aligned_episode.alignment_config,
            artifact.aligned_episode.alignment_semantics_version,
        )

        write_result = (
            await context.episode_artifact_store.write_aligned_episode_report(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                episode_id=params.episode_id,
                source_manifest_sha256=source_hash,
                alignment_key=key,
                report_kind="validation",
                report=report,
            )
        )

        report_artifact_id = generate_artifact_id()
        await context.artifact_record_store.create(
            artifact_id=report_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.ALIGNED_EPISODE_VALIDATION_REPORT,
                uri=write_result.uri,
                media_type="application/json",
                checksum=write_result.checksum,
                size_bytes=write_result.size_bytes,
                metadata={
                    "episode_id": params.episode_id,
                    "aligned_artifact_id": params.aligned_artifact_id,
                    "valid": report.valid,
                    "issue_count": len(report.issues),
                },
            ),
            owner_type=ArtifactOwnerType.EPISODE,
            owner_id=params.episode_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
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
            report_uri=write_result.uri,
        )
