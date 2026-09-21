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
    build_learning_signals_table,
    build_learning_steps_table,
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
    LEARNING_DATA_SCHEMA_VERSION,
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
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

_TABLE_BUILDERS_BY_NAME = {
    "learning_episodes": build_learning_episodes_table,
    "learning_steps": build_learning_steps_table,
    "learning_signals": build_learning_signals_table,
}


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

        for table_name in LEARNING_TABLE_BUILDERS:
            if table_name not in requested_tables:
                continue
            builder = _TABLE_BUILDERS_BY_NAME[table_name]
            df = builder(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
                entries=entries,
            )
            write_result = await context.analytics_writer.write_learning_table(
                table_name,
                df,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                export_id=export_id,
            )
            table_uris[table_name] = write_result.uri
            table_checksums[table_name] = write_result.checksum
            table_size_bytes[table_name] = write_result.size_bytes
            row_counts[table_name] = df.height

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

        return ExportLearningDataJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            episode_count=episode_count,
            table_uris=table_uris,
            row_counts=row_counts,
            manifest_artifact_id=manifest_artifact_id,
            manifest_uri=manifest_write_result.uri,
        )
