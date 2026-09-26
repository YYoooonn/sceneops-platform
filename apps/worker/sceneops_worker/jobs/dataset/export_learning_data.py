"""Explicit, revision-pinned AlignedEpisodeArtifacts -> columnar Parquet
export (SceneOps V2 Request 2.5).

Reuses _aligned_episode_resolution's resolve/verify/parse plumbing (already
shared with VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE, Request 2.4)
for every pinned input, then runs AlignedEpisodeValidator over each parsed
artifact and fails the whole export if any input is structurally invalid --
this job never produces a columnar snapshot with silently-broken inputs.
Building/writing the Parquet tables themselves is pure (sceneops-analytics'
learning_tables builders); this handler is resolution, validation gating,
and persistence only.
"""

from __future__ import annotations

from sceneops_analytics import (
    LEARNING_TABLE_BUILDERS,
    build_learning_episodes_table,
    write_sharded_learning_tables,
)
from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    AlignedEpisodeValidator,
)
from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_LAYOUT_VERSION_SHARDED,
    LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE,
    LEARNING_DATA_SCHEMA_VERSION,
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    LearningDataShardIndex,
    default_shard_policy,
    learning_data_export_id,
)
from sceneops_core.jobs.schemas import (
    ExportLearningDataJobParams,
    ExportLearningDataJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    resolve_and_verify_aligned_artifact,
)

_validator = AlignedEpisodeValidator()

# learning_episodes stays single-file (Request 5.2): it is always
# metadata-scale (one row per exposed EpisodeRef), so sharding it would add
# manifest/read complexity for no locality benefit. learning_steps/
# learning_signals are the sharded tables -- see write_sharded_learning_tables.
_SHARDED_TABLE_NAMES = frozenset({"learning_steps", "learning_signals"})
_SHARD_POLICY = default_shard_policy()


class ExportLearningDataJobHandler(
    JobHandler[ExportLearningDataJobParams, ExportLearningDataJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.EXPORT_LEARNING_DATA

    @property
    def params_model(self) -> type[ExportLearningDataJobParams]:
        return ExportLearningDataJobParams

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
        request: JobHandlerRequest[ExportLearningDataJobParams],
    ) -> ExportLearningDataJobResult:
        params = request.params
        context = request.context
        job = request.job

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        entries: list[tuple[str, AlignedEpisodeArtifact]] = []
        revisions: list[AlignedArtifactRevision] = []

        for item in params.inputs:
            record, artifact, _verified = await resolve_and_verify_aligned_artifact(
                context,
                episode_id=item.episode_id,
                aligned_artifact_id=item.aligned_artifact_id,
                expected_checksum=item.aligned_artifact_checksum,
            )
            checksum = item.aligned_artifact_checksum or record.checksum
            if checksum is None:
                raise ValueError(
                    "export_learning_data: no checksum available for "
                    f"aligned_artifact_id={item.aligned_artifact_id!r} "
                    f"(episode_id={item.episode_id!r})"
                )

            report = _validator.validate(artifact)
            if not report.valid:
                raise ValueError(
                    "export_learning_data: input aligned artifact failed "
                    f"structural validation (episode_id={item.episode_id!r}, "
                    f"aligned_artifact_id={item.aligned_artifact_id!r}, "
                    f"issue_count={len(report.issues)})"
                )

            entries.append((checksum, artifact))
            revisions.append(
                AlignedArtifactRevision(
                    episode_id=item.episode_id,
                    aligned_artifact_id=item.aligned_artifact_id,
                    aligned_artifact_checksum=checksum,
                )
            )

        export_config = LearningDataExportConfig(tables=params.tables)
        export_id = learning_data_export_id(
            aligned_checksums=[checksum for checksum, _ in entries],
            export_config=export_config,
            schema_version=LEARNING_DATA_SCHEMA_VERSION,
        )

        requested_tables = (
            set(params.tables) if params.tables else set(LEARNING_TABLE_BUILDERS)
        )

        table_uris: dict[str, str] = {}
        table_checksums: dict[str, str] = {}
        table_size_bytes: dict[str, int] = {}
        row_counts: dict[str, int] = {}

        if "learning_episodes" in requested_tables:
            df = build_learning_episodes_table(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
                entries=entries,
            )
            write_result = await context.analytics_writer.write_learning_table(
                "learning_episodes",
                df,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
            )
            table_uris["learning_episodes"] = write_result.uri
            table_checksums["learning_episodes"] = write_result.checksum
            table_size_bytes["learning_episodes"] = write_result.size_bytes
            row_counts["learning_episodes"] = df.height

        requested_sharded_tables = requested_tables & _SHARDED_TABLE_NAMES
        shard_index: LearningDataShardIndex | None = None
        if requested_sharded_tables:
            shard_index = await write_sharded_learning_tables(
                context.analytics_writer,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
                entries=entries,
                policy=_SHARD_POLICY,
                table_names=requested_sharded_tables,
            )
            for table_name in requested_sharded_tables:
                shards = getattr(shard_index, table_name)
                row_counts[table_name] = sum(shard.row_count for shard in shards)

        episode_count = len({item.episode_id for item in params.inputs})

        manifest = LearningDataExportManifest(
            schema_version=LEARNING_DATA_SCHEMA_VERSION,
            export_id=export_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            inputs=revisions,
            export_config=export_config,
            table_uris=table_uris,
            table_checksums=table_checksums,
            row_counts=row_counts,
            layout_version=(
                LEARNING_DATA_LAYOUT_VERSION_SHARDED
                if shard_index is not None
                else LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE
            ),
            shard_index=shard_index,
            episode_count=episode_count,
            metadata=params.metadata,
        )

        manifest_write_result = (
            await context.analytics_writer.write_learning_export_manifest(
                manifest,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
            )
        )

        owner_id = f"{dataset_id}:{dataset_version}"

        for table_name, uri in table_uris.items():
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.ANALYTICS_TABLE,
                    uri=uri,
                    media_type="application/vnd.apache.parquet",
                    checksum=table_checksums[table_name],
                    size_bytes=table_size_bytes[table_name],
                    metadata={"export_id": export_id, "table_name": table_name},
                ),
                owner_type=ArtifactOwnerType.DATASET_VERSION,
                owner_id=owner_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        # One ArtifactRecord per physical shard file (SceneOps V2 Request
        # 5.2) -- every shard is its own real, independently-checksummed
        # artifact and gets its own lineage record, exactly like every
        # other physical Parquet object this job writes above.
        if shard_index is not None:
            for table_name in requested_sharded_tables:
                for shard in getattr(shard_index, table_name):
                    await context.artifact_record_store.create(
                        artifact_id=generate_artifact_id(),
                        ref=ArtifactRef(
                            kind=ArtifactKind.ANALYTICS_TABLE,
                            uri=shard.uri,
                            media_type="application/vnd.apache.parquet",
                            checksum=shard.checksum,
                            size_bytes=shard.size_bytes,
                            metadata={
                                "export_id": export_id,
                                "table_name": table_name,
                                "shard_index": shard.shard_index,
                            },
                        ),
                        owner_type=ArtifactOwnerType.DATASET_VERSION,
                        owner_id=owner_id,
                        dataset_id=dataset_id,
                        dataset_version=dataset_version,
                        job_id=job.job_id,
                        pipeline_run_id=job.pipeline_run_id,
                    )

        manifest_artifact_id = generate_artifact_id()
        await context.artifact_record_store.create(
            artifact_id=manifest_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
                uri=manifest_write_result.uri,
                media_type="application/json",
                checksum=manifest_write_result.checksum,
                size_bytes=manifest_write_result.size_bytes,
                metadata={"export_id": export_id, "episode_count": episode_count},
            ),
            owner_type=ArtifactOwnerType.DATASET_VERSION,
            owner_id=owner_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        await context.commit()

        shard_counts = (
            {
                table_name: len(getattr(shard_index, table_name))
                for table_name in requested_sharded_tables
            }
            if shard_index is not None
            else {}
        )

        return ExportLearningDataJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            episode_count=episode_count,
            table_uris=table_uris,
            row_counts=row_counts,
            shard_counts=shard_counts,
            manifest_artifact_id=manifest_artifact_id,
            manifest_uri=manifest_write_result.uri,
        )
