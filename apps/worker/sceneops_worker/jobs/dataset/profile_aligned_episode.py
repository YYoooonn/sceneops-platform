from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.derived_ids import derived_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    AlignedEpisodeProfiler,
    aligned_episode_alignment_key,
)
from sceneops_core.jobs.schemas import (
    JobType,
    ProfileAlignedEpisodeJobParams,
    ProfileAlignedEpisodeJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    resolve_and_verify_aligned_artifact,
)

_profiler = AlignedEpisodeProfiler()


class ProfileAlignedEpisodeJobHandler(
    JobHandler[ProfileAlignedEpisodeJobParams, ProfileAlignedEpisodeJobResult]
):
    """AlignedEpisodeArtifact -> descriptive AlignedEpisodeProfile (SceneOps
    V2 Request 2.4).

    Never runs validation as a side effect -- composable, independent of
    VALIDATE_ALIGNED_EPISODE (Request 2.4 §36). All metric definitions live
    in sceneops-core's pure AlignedEpisodeProfiler; this handler is
    resolution, checksum verification, and persistence only.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.PROFILE_ALIGNED_EPISODE

    @property
    def params_model(self) -> type[ProfileAlignedEpisodeJobParams]:
        return ProfileAlignedEpisodeJobParams

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
        request: JobHandlerRequest[ProfileAlignedEpisodeJobParams],
    ) -> ProfileAlignedEpisodeJobResult:
        params = request.params
        context = request.context
        job = request.job

        record, artifact, _verified = await resolve_and_verify_aligned_artifact(
            context,
            episode_id=params.episode_id,
            aligned_artifact_id=params.aligned_artifact_id,
            expected_checksum=params.aligned_artifact_checksum,
        )
        # The aligned artifact carries its DatasetVersion scope; never a default.
        dataset_id = record.dataset_id
        dataset_version = record.dataset_version
        if (params.dataset_id, params.dataset_version) not in (
            (None, None),
            (dataset_id, dataset_version),
        ):
            raise ValueError(
                f"aligned artifact {params.aligned_artifact_id} belongs to "
                f"{dataset_id}:{dataset_version}, not "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        profile = _profiler.profile(artifact)

        source_hash = artifact.source_revision.source_manifest_sha256
        key = aligned_episode_alignment_key(artifact.aligned_episode)

        write_result = (
            await context.episode_artifact_store.write_aligned_episode_report(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                episode_id=params.episode_id,
                source_manifest_sha256=source_hash,
                alignment_key=key,
                report_kind="profile",
                report=profile,
            )
        )

        report_artifact_id = derived_artifact_id(
            prefix="alignedprofile",
            logical_id=params.aligned_artifact_id,
            checksum=write_result.checksum,
        )
        await context.artifact_record_store.register(
            artifact_id=report_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.ALIGNED_EPISODE_PROFILE_REPORT,
                uri=write_result.uri,
                media_type="application/json",
                checksum=write_result.checksum,
                size_bytes=write_result.size_bytes,
                metadata={
                    "episode_id": params.episode_id,
                    "aligned_artifact_id": params.aligned_artifact_id,
                    "step_count": profile.step_count,
                    "overall_missing_ratio": profile.overall_missing_ratio,
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

        return ProfileAlignedEpisodeJobResult(
            episode_id=params.episode_id,
            aligned_artifact_id=params.aligned_artifact_id,
            aligned_artifact_checksum=params.aligned_artifact_checksum,
            profile_semantics_version=profile.profile_semantics_version,
            step_count=profile.step_count,
            observation_channel_count=profile.observation_channel_count,
            action_channel_count=profile.action_channel_count,
            overall_missing_ratio=profile.overall_missing_ratio,
            max_channel_missing_ratio=profile.max_channel_missing_ratio,
            report_artifact_id=report_artifact_id,
            report_uri=write_result.uri,
        )
