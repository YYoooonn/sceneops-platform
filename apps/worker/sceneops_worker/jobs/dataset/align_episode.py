from __future__ import annotations

import hashlib

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    MCAP_LOG_TIME_CLOCK,
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalSourceContext,
    align_episode,
    alignment_config_hash,
    alignment_key,
)
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_core.jobs.schemas import (
    AlignEpisodeJobParams,
    AlignEpisodeJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_db.queries import resolve_current_episode_manifest_source
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class SourceManifestNotFoundError(Exception):
    """No EPISODE_MANIFEST ArtifactRecord (or its bytes) could be resolved
    for the requested Episode / pinned source revision."""


class SourceRevisionMismatchError(Exception):
    """The bytes actually read do not match the expected source-revision
    checksum (either the caller's pinned source_manifest_sha256, or the
    resolved ArtifactRecord's own checksum). Alignment refuses to run
    rather than silently pairing one revision's artifact_id with another
    revision's bytes (SceneOps V2 Request 2.3 §8)."""


class AlignEpisodeJobHandler(JobHandler[AlignEpisodeJobParams, AlignEpisodeJobResult]):
    """EpisodeManifest + TemporalAlignmentConfig -> AlignedEpisodeArtifact
    (SceneOps V2 Request 2.3).

    Resolves one exact EPISODE_MANIFEST source revision, verifies its
    content against a checksum before parsing it, calls the already-frozen
    pure sceneops-core align_episode() engine unchanged, and persists the
    result through the same "producer owns the ArtifactRecord" pattern
    every other Episode-domain job already follows. No temporal alignment
    math lives here — see sceneops_core.episodes.alignment for that;
    this handler is I/O, resolution, and persistence only.

    Standalone Job, no PipelineType yet (Request 2.3 §29) — a validation/
    profile pipeline can wrap this once Request 2.4 exists.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.ALIGN_EPISODE

    @property
    def params_model(self) -> type[AlignEpisodeJobParams]:
        return AlignEpisodeJobParams

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
        request: JobHandlerRequest[AlignEpisodeJobParams],
    ) -> AlignEpisodeJobResult:
        params = request.params
        context = request.context
        job = request.job

        dataset_id = params.dataset_id or context.default_dataset_id
        dataset_version = params.dataset_version or context.default_dataset_version

        episode_record = await context.episode_store.get(params.episode_id)
        if episode_record is None:
            raise ValueError(f"Episode not found: {params.episode_id}")

        source_artifact_id, source_uri, source_checksum = await self._resolve_source(
            context=context,
            params=params,
            episode_id=params.episode_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            expected_uri=episode_record.episode_manifest_uri,
        )

        raw_bytes = await context.episode_artifact_store.read_episode_manifest_bytes(
            source_uri
        )
        if raw_bytes is None:
            raise SourceManifestNotFoundError(
                f"Source EPISODE_MANIFEST bytes not found at {source_uri!r} "
                f"(artifact_id={source_artifact_id!r})"
            )

        computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        source_checksum_verified = self._verify_checksum(
            params=params,
            source_checksum=source_checksum,
            computed_sha256=computed_sha256,
            episode_id=params.episode_id,
            source_artifact_id=source_artifact_id,
        )

        manifest = EpisodeManifest.model_validate_json(raw_bytes)

        source_context = params.source_context or TemporalSourceContext(
            source_clock=MCAP_LOG_TIME_CLOCK
        )

        aligned = align_episode(manifest, params.alignment_config, source_context)

        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id=params.episode_id,
                episode_manifest_uri=source_uri,
                source_artifact_id=source_artifact_id,
                source_manifest_sha256=computed_sha256,
            ),
            aligned_episode=aligned,
        )

        config_hash = alignment_config_hash(params.alignment_config)
        key = alignment_key(
            params.alignment_config, aligned.alignment_semantics_version
        )

        write_result = await context.episode_artifact_store.write_aligned_episode(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=params.episode_id,
            source_manifest_sha256=computed_sha256,
            alignment_key=key,
            artifact=artifact,
        )

        aligned_artifact_id = generate_artifact_id()
        await context.artifact_record_store.create(
            artifact_id=aligned_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.ALIGNED_EPISODE_MANIFEST,
                uri=write_result.uri,
                media_type="application/json",
                checksum=write_result.checksum,
                size_bytes=write_result.size_bytes,
                metadata={
                    "episode_id": params.episode_id,
                    "source_artifact_id": source_artifact_id,
                    "source_manifest_sha256": computed_sha256,
                    "alignment_config_hash": config_hash,
                    "alignment_semantics_version": aligned.alignment_semantics_version,
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

        return AlignEpisodeJobResult(
            episode_id=params.episode_id,
            aligned_artifact_id=aligned_artifact_id,
            aligned_artifact_uri=write_result.uri,
            source_artifact_id=source_artifact_id,
            source_manifest_sha256=computed_sha256,
            source_checksum_verified=source_checksum_verified,
            alignment_semantics_version=aligned.alignment_semantics_version,
            alignment_config_hash=config_hash,
            step_count=aligned.step_count,
            target_frequency_hz=aligned.target_frequency_hz,
            achieved_frequency_hz=aligned.achieved_frequency_hz,
            duplicate_discarded_count=aligned.duplicate_discarded_count,
        )

    # ── source resolution ───────────────────────────────────────────────

    @staticmethod
    async def _resolve_source(
        *,
        context: WorkerContext,
        params: AlignEpisodeJobParams,
        episode_id: str,
        dataset_id: str,
        dataset_version: str,
        expected_uri: str | None,
    ) -> tuple[str, str, str | None]:
        """Returns (source_artifact_id, episode_manifest_uri, checksum).

        Pinned (params.source_artifact_id set): resolve that exact
        ArtifactRecord and validate it actually is an EPISODE_MANIFEST
        owned by this episode.

        Unpinned (the common case): the "current" source is the latest
        EPISODE_MANIFEST ArtifactRecord for this episode by created_at —
        the same "latest wins" convention already used everywhere else in
        this platform (run records, validation/profile results), not a
        new selection rule invented for this handler (SceneOps V2 Request
        2.3 §7). Cross-checked against EpisodeRecord.episode_manifest_uri
        as a defensive consistency check, not the primary selection rule.
        """
        if params.source_artifact_id is not None:
            record = await context.artifact_record_store.get(params.source_artifact_id)
            if record is None or record.kind != ArtifactKind.EPISODE_MANIFEST.value:
                raise SourceManifestNotFoundError(
                    f"Pinned source_artifact_id {params.source_artifact_id!r} is not "
                    "a valid EPISODE_MANIFEST ArtifactRecord"
                )
            if (
                record.owner_type != ArtifactOwnerType.EPISODE.value
                or record.owner_id != episode_id
            ):
                raise SourceManifestNotFoundError(
                    f"Pinned source_artifact_id {params.source_artifact_id!r} does not "
                    f"belong to episode {episode_id!r}"
                )
            return record.artifact_id, record.uri, record.checksum

        # SceneOps V2 Request 2.3A §5/§13: shared with the API's
        # job-creation-time source resolution -- one selection rule, not two.
        record = await resolve_current_episode_manifest_source(
            context.artifact_record_store,
            episode_id=episode_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        if record is None:
            raise SourceManifestNotFoundError(
                f"No EPISODE_MANIFEST ArtifactRecord found for episode {episode_id!r}"
            )
        if expected_uri is not None and record.uri != expected_uri:
            raise SourceManifestNotFoundError(
                f"Latest EPISODE_MANIFEST ArtifactRecord uri {record.uri!r} does not "
                f"match EpisodeRecord.episode_manifest_uri {expected_uri!r} for "
                f"episode {episode_id!r}"
            )
        return record.artifact_id, record.uri, record.checksum

    # ── checksum verification ───────────────────────────────────────────

    @staticmethod
    def _verify_checksum(
        *,
        params: AlignEpisodeJobParams,
        source_checksum: str | None,
        computed_sha256: str,
        episode_id: str,
        source_artifact_id: str,
    ) -> bool:
        """Returns whether the source revision was checksum-verified.

        Caller-pinned source_manifest_sha256 takes precedence as the
        stronger, explicit assertion. Otherwise falls back to the resolved
        ArtifactRecord's own checksum, if populated. A legacy record with
        no checksum and no caller pin is not a failure (SceneOps V2 Request
        2.3 §9) — it proceeds content-verified-at-read-time (the hash is
        real and gets recorded) but producer-revision-unverified (nothing
        confirms it matches what build_episodes originally wrote).
        """
        if params.source_manifest_sha256 is not None:
            if params.source_manifest_sha256 != computed_sha256:
                raise SourceRevisionMismatchError(
                    f"Source manifest checksum mismatch for episode "
                    f"{episode_id!r}: pinned source_manifest_sha256="
                    f"{params.source_manifest_sha256!r}, actual bytes hash to "
                    f"{computed_sha256!r}"
                )
            return True

        if source_checksum is not None:
            expected = source_checksum.removeprefix("sha256:")
            if expected != computed_sha256:
                raise SourceRevisionMismatchError(
                    f"Source manifest checksum mismatch for episode "
                    f"{episode_id!r}: ArtifactRecord {source_artifact_id!r} "
                    f"declares checksum={source_checksum!r}, actual bytes hash to "
                    f"sha256:{computed_sha256}"
                )
            return True

        return False
