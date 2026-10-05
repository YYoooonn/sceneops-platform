from __future__ import annotations

import hashlib

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.derived_ids import aligned_episode_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalSourceContext,
    align_episode,
    alignment_config_hash,
    alignment_key,
)
from sceneops_core.episodes.schemas import load_canonical_episode_manifest
from sceneops_core.jobs.schemas import (
    AlignEpisodeJobParams,
    AlignEpisodeJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.episodes.schemas import EpisodeRecord
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
    """Canonical Episode + TemporalAlignmentConfig -> AlignedEpisodeArtifact
    (derived, L3; ADR-007 §13.10, §31.8).

    Resolves one exact canonical EpisodeManifest revision (the one the
    EpisodeRecord points to, or an explicitly pinned one), verifies its
    bytes against their checksum, parses them strictly, runs the pure
    ``align_episode`` engine and persists the result with the source
    revision it consumed. All timeline / association decisions live in the
    engine; canonical Episode data is never rewritten.
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

        episode_record = await context.episode_store.get(params.episode_id)
        if episode_record is None:
            raise ValueError(f"Episode not found: {params.episode_id}")
        # An Episode belongs to exactly one DatasetVersion: that is the scope,
        # never a configured default.
        dataset_id = episode_record.dataset_id
        dataset_version = episode_record.dataset_version
        if (params.dataset_id, params.dataset_version) not in (
            (None, None),
            (dataset_id, dataset_version),
        ):
            raise ValueError(
                f"Episode {params.episode_id} belongs to {dataset_id}:"
                f"{dataset_version}, not {params.dataset_id}:{params.dataset_version}"
            )

        source_artifact_id, source_uri, source_checksum = await self._resolve_source(
            context=context, params=params, episode_record=episode_record
        )
        raw_bytes = await context.episode_artifact_store.read_pinned_manifest_bytes(
            uri=source_uri, checksum=source_checksum
        )
        computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        source_checksum_verified = self._verify_checksum(
            params=params,
            source_checksum=source_checksum,
            computed_sha256=computed_sha256,
            episode_id=params.episode_id,
            source_artifact_id=source_artifact_id,
        )

        manifest = load_canonical_episode_manifest(raw_bytes)
        source_context = params.source_context or TemporalSourceContext(
            source_clock=manifest.declared_window().source_clock
        )

        aligned = align_episode(
            manifest,
            params.alignment_config,
            source_context,
            episode_id=params.episode_id,
        )

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
            params.alignment_config,
            aligned.alignment_semantics_version,
            aligned.source_clock,
        )

        write_result = await context.episode_artifact_store.write_aligned_episode(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=params.episode_id,
            source_manifest_sha256=computed_sha256,
            alignment_key=key,
            artifact=artifact,
        )

        aligned_artifact_id = aligned_episode_artifact_id(
            episode_id=params.episode_id, checksum=write_result.checksum
        )
        await context.artifact_record_store.register(
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
                    "source_clock": aligned.source_clock,
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
            aligned_artifact_checksum=write_result.checksum,
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
        episode_record: EpisodeRecord,
    ) -> tuple[str, str, str]:
        """Returns (source_artifact_id, uri, checksum).

        Pinned (params.source_artifact_id set): that exact EPISODE_MANIFEST
        ArtifactRecord of this episode, which may be an earlier revision.
        Unpinned: exactly the revision the EpisodeRecord points to
        (``manifest_artifact_id``), never "the latest artifact" (§14.4).
        """
        artifact_id = params.source_artifact_id or episode_record.manifest_artifact_id
        record = await context.artifact_record_store.get(artifact_id)
        if record is None or record.kind != ArtifactKind.EPISODE_MANIFEST.value:
            raise SourceManifestNotFoundError(
                f"source_artifact_id {artifact_id!r} is not a valid EPISODE_MANIFEST "
                "ArtifactRecord"
            )
        if (
            record.owner_type != ArtifactOwnerType.EPISODE.value
            or record.owner_id != episode_record.episode_id
        ):
            raise SourceManifestNotFoundError(
                f"source_artifact_id {artifact_id!r} does not belong to episode "
                f"{episode_record.episode_id!r}"
            )
        if not record.checksum:
            raise SourceManifestNotFoundError(
                f"EPISODE_MANIFEST {artifact_id!r} pins no checksum"
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
        stronger, explicit assertion; otherwise the resolved ArtifactRecord's
        own checksum is verified.
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
