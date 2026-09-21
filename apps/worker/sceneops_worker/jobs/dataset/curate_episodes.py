"""Explicit, revision-pinned LearningDataExportManifest -> selected/rejected
AlignedEpisode revisions (SceneOps V2 Request 2.6).

Resolves the pinned LEARNING_DATA_EXPORT_MANIFEST once (candidate-set
boundary, Request 2.6 §3), then for each of its pinned
AlignedArtifactRevision entries resolves+verifies the exact
AlignedEpisodeArtifact (reusing _aligned_episode_resolution's plumbing,
already shared with VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE/
EXPORT_LEARNING_DATA) and recomputes its structural validation + descriptive
profile fresh via Request 2.4's own pure classes -- the same "recompute
over resolved+checksum-verified bytes, persist only for audit" convention
those job handlers already establish, never a lookup of a separately
persisted report (this also means required profile/validation data is
never unavailable: it is a deterministic function of bytes this handler
must resolve anyway).

CurationEvaluator (pure, sceneops-core) is the only place selection logic
lives; this handler is resolution, fact-gathering, and persistence only.
Never mutates Episode/AlignedEpisode/DatasetVersion state and never
rewrites the source columnar Parquet (Request 2.6 §14/§17).
"""

from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
    ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
    AlignedEpisodeProfiler,
    AlignedEpisodeValidator,
    ChannelNamespace,
)
from sceneops_core.episodes.curation import (
    CURATION_SEMANTICS_VERSION,
    CurationCandidateFacts,
    CurationCandidateSummary,
    CurationEvaluator,
    EpisodeCurationManifest,
    SourceLearningExportRef,
    curation_policy_hash,
    episode_curation_id,
)
from sceneops_core.jobs.schemas import (
    CurateEpisodesJobParams,
    CurateEpisodesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    resolve_and_verify_aligned_artifact,
)
from sceneops_worker.jobs.dataset._learning_export_resolution import (
    resolve_and_verify_learning_export_manifest,
)

_validator = AlignedEpisodeValidator()
_profiler = AlignedEpisodeProfiler()
_evaluator = CurationEvaluator()


class CurateEpisodesJobHandler(
    JobHandler[CurateEpisodesJobParams, CurateEpisodesJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.CURATE_EPISODES

    @property
    def params_model(self) -> type[CurateEpisodesJobParams]:
        return CurateEpisodesJobParams

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
        request: JobHandlerRequest[CurateEpisodesJobParams],
    ) -> CurateEpisodesJobResult:
        params = request.params
        context = request.context
        job = request.job

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        (
            manifest_record,
            export_manifest,
            _verified,
        ) = await resolve_and_verify_learning_export_manifest(
            context,
            manifest_artifact_id=params.learning_data_export_manifest_artifact_id,
            expected_checksum=params.learning_data_export_manifest_checksum,
        )
        source_export_checksum = (
            params.learning_data_export_manifest_checksum or manifest_record.checksum
        )
        if source_export_checksum is None:
            raise ValueError(
                "curate_episodes: no checksum available for "
                "learning_data_export_manifest_artifact_id="
                f"{params.learning_data_export_manifest_artifact_id!r}"
            )

        decisions = []
        for item in export_manifest.inputs:
            _record, artifact, _verified2 = await resolve_and_verify_aligned_artifact(
                context,
                episode_id=item.episode_id,
                aligned_artifact_id=item.aligned_artifact_id,
                expected_checksum=item.aligned_artifact_checksum,
            )

            report = _validator.validate(artifact)
            if not report.valid:
                facts = CurationCandidateFacts(
                    episode_id=item.episode_id,
                    aligned_artifact_checksum=item.aligned_artifact_checksum,
                    valid=False,
                    validation_issue_count=len(report.issues),
                )
            else:
                profile = _profiler.profile(artifact)
                ae = artifact.aligned_episode
                observation_channels = sorted(
                    cp.channel
                    for cp in profile.channel_profiles
                    if cp.namespace == ChannelNamespace.OBSERVATION
                )
                action_channels = sorted(
                    cp.channel
                    for cp in profile.channel_profiles
                    if cp.namespace == ChannelNamespace.ACTION
                )
                max_interpolated_ratio = max(
                    (cp.interpolated_ratio for cp in profile.channel_profiles),
                    default=0.0,
                )
                facts = CurationCandidateFacts(
                    episode_id=item.episode_id,
                    aligned_artifact_checksum=item.aligned_artifact_checksum,
                    valid=True,
                    validation_issue_count=len(report.issues),
                    task=ae.task,
                    outcome=ae.outcome,
                    observation_channels=observation_channels,
                    action_channels=action_channels,
                    overall_missing_ratio=profile.overall_missing_ratio,
                    max_channel_missing_ratio=profile.max_channel_missing_ratio,
                    max_interpolated_ratio=max_interpolated_ratio,
                    max_abs_sync_delta_us=profile.max_abs_sync_delta_us,
                )

            decisions.append(_evaluator.evaluate(facts, params.policy))

        # Deterministic manifest ordering, independent of
        # export_manifest.inputs iteration order (Request 2.6 §15).
        decisions.sort(key=lambda d: (d.episode_id, d.aligned_artifact_checksum))

        selected = [d for d in decisions if d.selected]
        rejected = [d for d in decisions if not d.selected]

        # The exact validator/profiler semantics versions used above to
        # produce every candidate's facts in this run (SceneOps V2 Request
        # 2.6A §1/§2/§3) -- participate in curation_id and are persisted
        # verbatim on the manifest, so neither depends on inferring "the
        # currently-installed package version" at read time.
        policy_hash = curation_policy_hash(params.policy)
        curation_id = episode_curation_id(
            source_export_checksum=source_export_checksum,
            policy_hash=policy_hash,
            curation_semantics_version=CURATION_SEMANTICS_VERSION,
            validation_semantics_version=ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
            profile_semantics_version=ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
        )

        manifest = EpisodeCurationManifest(
            curation_id=curation_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source_learning_export=SourceLearningExportRef(
                artifact_id=params.learning_data_export_manifest_artifact_id,
                checksum=source_export_checksum,
            ),
            policy=params.policy,
            policy_hash=policy_hash,
            curation_semantics_version=CURATION_SEMANTICS_VERSION,
            validation_semantics_version=ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
            profile_semantics_version=ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
            candidates=CurationCandidateSummary(
                total=len(decisions),
                selected=len(selected),
                rejected=len(rejected),
            ),
            decisions=decisions,
            selected_aligned_artifact_checksums=sorted(
                d.aligned_artifact_checksum for d in selected
            ),
            metadata=params.metadata,
        )

        write_result = await context.analytics_writer.write_curation_manifest(
            manifest,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            curation_id=curation_id,
        )

        owner_id = f"{dataset_id}:{dataset_version}"
        manifest_artifact_id = generate_artifact_id()
        await context.artifact_record_store.create(
            artifact_id=manifest_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.EPISODE_CURATION_MANIFEST,
                uri=write_result.uri,
                media_type="application/json",
                checksum=write_result.checksum,
                size_bytes=write_result.size_bytes,
                metadata={
                    "curation_id": curation_id,
                    "selected_count": len(selected),
                    "rejected_count": len(rejected),
                },
            ),
            owner_type=ArtifactOwnerType.DATASET_VERSION,
            owner_id=owner_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        await context.commit()

        return CurateEpisodesJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            curation_id=curation_id,
            candidate_count=len(decisions),
            selected_count=len(selected),
            rejected_count=len(rejected),
            manifest_artifact_id=manifest_artifact_id,
            manifest_uri=write_result.uri,
        )
