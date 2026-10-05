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

import polars as pl

from sceneops_analytics import (
    LEARNING_TABLE_BUILDERS,
    build_learning_episodes_table,
    write_incremental_sharded_learning_tables,
    write_sharded_learning_tables,
)
from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.derived_ids import derived_artifact_id
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
    IncrementalExportPlan,
    LearningDataExportConfig,
    LearningDataExportManifest,
    LearningDataShardIndex,
    default_shard_policy,
    learning_data_export_id,
    plan_incremental_export,
)
from sceneops_core.jobs.schemas import (
    ExportLearningDataJobParams,
    ExportLearningDataJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.core.context import WorkerContext
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


class BaseLearningExportNotFoundError(Exception):
    """``base_export_id`` has no manifest at its expected content-addressed
    URI -- either the export_id is wrong, or that export was never actually
    published. No separate checksum-pinning is needed here (unlike
    manifest_artifact_id-based resolution elsewhere): export_id is itself a
    content hash of the base export's own inputs/config (SceneOps V2 Request
    5.5 §6), so resolving by export_id already is resolving by content."""


async def _resolve_base_export_manifest(
    context: WorkerContext,
    *,
    dataset_id: str,
    dataset_version: str,
    base_export_id: str,
) -> LearningDataExportManifest:
    uri = context.analytics_writer.learning_export_manifest_uri(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=base_export_id,
    )
    raw_bytes = await context.analytics_writer.read_learning_export_manifest_bytes(uri)
    if raw_bytes is None:
        raise BaseLearningExportNotFoundError(
            f"base_export_id {base_export_id!r} has no manifest at {uri!r} "
            f"(dataset_id={dataset_id!r}, dataset_version={dataset_version!r})"
        )
    return LearningDataExportManifest.model_validate_json(raw_bytes)


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
        params: JsonDict = {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }
        # In a pipeline the inputs are the aligned revisions the upstream
        # align_episode stage published, pinned by checksum.
        if "inputs" not in params and inputs.refs.get("export_inputs"):
            params["inputs"] = inputs.refs["export_inputs"]
        return params

    async def run(
        self,
        request: JobHandlerRequest[ExportLearningDataJobParams],
    ) -> ExportLearningDataJobResult:
        params = request.params
        context = request.context
        job = request.job

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        base_manifest: LearningDataExportManifest | None = None
        if params.base_export_id is not None:
            base_manifest = await _resolve_base_export_manifest(
                context,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                base_export_id=params.base_export_id,
            )

        # entries/revisions are only the delta being added (SceneOps V2
        # Request 5.5 §1) when base_manifest is set -- merged with
        # base_manifest.inputs below to get the full target set.
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

        # The full target set: base's own inputs plus this delta (identical
        # to `revisions` when this is an ordinary, non-incremental export).
        all_revisions = (
            [*base_manifest.inputs, *revisions]
            if base_manifest is not None
            else revisions
        )

        export_config = LearningDataExportConfig(tables=params.tables)
        export_id = learning_data_export_id(
            aligned_checksums=[r.aligned_artifact_checksum for r in all_revisions],
            export_config=export_config,
            schema_version=LEARNING_DATA_SCHEMA_VERSION,
        )

        requested_tables = (
            set(params.tables) if params.tables else set(LEARNING_TABLE_BUILDERS)
        )

        incremental_plan: IncrementalExportPlan | None = None
        if base_manifest is not None:
            incremental_plan = plan_incremental_export(base_manifest, all_revisions)
            for table_name in _SHARDED_TABLE_NAMES:
                base_has_table = bool(
                    base_manifest.shard_index
                    and getattr(base_manifest.shard_index, table_name)
                )
                requested = table_name in requested_tables
                if base_has_table and not requested:
                    raise ValueError(
                        "export_learning_data: incremental export must "
                        f"include {table_name!r} -- base export "
                        f"{params.base_export_id!r} already includes it"
                    )
                if requested and not base_has_table:
                    raise ValueError(
                        f"export_learning_data: incremental export cannot "
                        f"add {table_name!r} -- base export "
                        f"{params.base_export_id!r} does not include it; "
                        "use a full export instead"
                    )

        table_uris: dict[str, str] = {}
        table_checksums: dict[str, str] = {}
        table_size_bytes: dict[str, int] = {}
        row_counts: dict[str, int] = {}

        if "learning_episodes" in requested_tables:
            delta_df = build_learning_episodes_table(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
                entries=entries,
            )
            base_episodes_uri = (
                base_manifest.table_uris.get("learning_episodes")
                if base_manifest is not None
                else None
            )
            if base_episodes_uri is not None:
                # Reused rows keep their original export_id/dataset_id/
                # dataset_version column values (SceneOps V2 Request 5.5
                # §3) -- SceneOpsDataset never reads these columns, so
                # rewriting them would only add risk for no benefit.
                base_df = await context.analytics_writer.read_learning_table(
                    base_episodes_uri
                )
                df = pl.concat([base_df, delta_df], how="vertical")
            else:
                df = delta_df
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
        reused_shard_counts: dict[str, int] = {}
        new_shard_counts: dict[str, int] = {}
        reused_shard_uris: set[str] = set()
        if requested_sharded_tables:
            if incremental_plan is not None:
                shard_index = await write_incremental_sharded_learning_tables(
                    context.analytics_writer,
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                    export_id=export_id,
                    delta_entries=entries,
                    policy=_SHARD_POLICY,
                    plan=incremental_plan,
                    table_names=requested_sharded_tables,
                )
                for table_name in requested_sharded_tables:
                    reused = getattr(incremental_plan, f"reused_{table_name}_shards")
                    reused_shard_uris.update(shard.uri for shard in reused)
                    reused_shard_counts[table_name] = len(reused)
                    new_shard_counts[table_name] = len(
                        getattr(shard_index, table_name)
                    ) - len(reused)
            else:
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

        episode_count = len({r.episode_id for r in all_revisions})

        manifest = LearningDataExportManifest(
            schema_version=LEARNING_DATA_SCHEMA_VERSION,
            export_id=export_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            inputs=all_revisions,
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
            base_export_id=params.base_export_id,
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
            await context.artifact_record_store.register(
                artifact_id=derived_artifact_id(
                    prefix="lexporttable",
                    logical_id=f"{export_id}:{table_name}",
                    checksum=table_checksums[table_name],
                ),
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
        # other physical Parquet object this job writes above. Shards
        # reused verbatim from an incremental export's base (SceneOps V2
        # Request 5.5 §5) are skipped here -- their base export's own
        # ArtifactRecord remains the sole, correct lineage entry; creating
        # a second record for the same unchanged uri/checksum would be a
        # duplicate, not real lineage.
        if shard_index is not None:
            for table_name in requested_sharded_tables:
                for shard in getattr(shard_index, table_name):
                    if shard.uri in reused_shard_uris:
                        continue
                    await context.artifact_record_store.register(
                        artifact_id=derived_artifact_id(
                            prefix="lexportshard",
                            logical_id=f"{export_id}:{table_name}:{shard.shard_index}",
                            checksum=shard.checksum,
                        ),
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

        manifest_artifact_id = derived_artifact_id(
            prefix="lexport",
            logical_id=export_id,
            checksum=manifest_write_result.checksum,
        )
        await context.artifact_record_store.register(
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
            base_export_id=params.base_export_id,
            reused_shard_counts=reused_shard_counts,
            new_shard_counts=new_shard_counts,
            manifest_artifact_id=manifest_artifact_id,
            manifest_uri=manifest_write_result.uri,
        )
