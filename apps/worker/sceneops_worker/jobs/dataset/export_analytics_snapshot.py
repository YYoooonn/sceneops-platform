from __future__ import annotations

from sceneops_analytics import (
    TABLE_BUILDERS,
    AnalyticsTableWriteResult,
    build_keyframes_table,
    build_observations_table,
    build_scenes_table,
)
from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    ExportAnalyticsSnapshotJobParams,
    ExportAnalyticsSnapshotJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.scenes.schemas import SceneManifest
from sceneops_worker.derived.publication import register_published
from sceneops_worker.scenes.resolver import list_dataset_version_scenes
from sceneops_worker.scenes.resolver import resolve_registered_scene
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest

_MANIFEST_TABLE_BUILDERS = {
    "observations": build_observations_table,
    "keyframes": build_keyframes_table,
}


class ExportAnalyticsSnapshotJobHandler(
    JobHandler[ExportAnalyticsSnapshotJobParams, ExportAnalyticsSnapshotJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.EXPORT_ANALYTICS_SNAPSHOT

    @property
    def params_model(self) -> type[ExportAnalyticsSnapshotJobParams]:
        return ExportAnalyticsSnapshotJobParams

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
        request: JobHandlerRequest[ExportAnalyticsSnapshotJobParams],
    ) -> ExportAnalyticsSnapshotJobResult:
        job = request.job
        params = request.params
        context = request.context

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        requested_tables = set(params.tables) if params.tables else set(TABLE_BUILDERS)

        # Analytics tables are a full snapshot of the current DB/manifest state —
        # always query all registered scenes of the DatasetVersion.
        all_scene_records = await list_dataset_version_scenes(
            context, dataset_id=dataset_id, dataset_version=dataset_version
        )

        if not all_scene_records:
            raise ValueError(
                f"export_analytics_snapshot: no registered scenes found for "
                f"dataset_id={dataset_id!r}, dataset_version={dataset_version!r}. "
                "Ensure register_scenes has completed before exporting analytics."
            )

        # Every Scene is read at the revision its record pins, verified.
        manifests: list[tuple[str, SceneManifest]] = []
        if requested_tables & set(_MANIFEST_TABLE_BUILDERS):
            for scene in all_scene_records:
                resolved = await resolve_registered_scene(context, scene)
                manifests.append((scene.scene_id, resolved.manifest))

        tables: dict[str, AnalyticsTableWriteResult] = {}
        row_counts: dict[str, int] = {}

        if "scenes" in requested_tables:
            df = build_scenes_table(all_scene_records)
            tables["scenes"] = await context.analytics_writer.write_table(
                "scenes", df, dataset_id=dataset_id, dataset_version=dataset_version
            )
            row_counts["scenes"] = df.height

        for table_name, builder in _MANIFEST_TABLE_BUILDERS.items():
            if table_name not in requested_tables:
                continue
            df = builder(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                manifests=manifests,
            )
            tables[table_name] = await context.analytics_writer.write_table(
                table_name, df, dataset_id=dataset_id, dataset_version=dataset_version
            )
            row_counts[table_name] = df.height

        for table_name, table in tables.items():
            await register_published(
                context,
                kind=ArtifactKind.ANALYTICS_TABLE,
                prefix="analyticstable",
                logical_id=f"{dataset_id}:{dataset_version}:{table_name}",
                uri=table.uri,
                checksum=table.checksum,
                size_bytes=table.size_bytes,
                media_type="application/vnd.apache.parquet",
                metadata={"table_name": table_name},
                owner_type=ArtifactOwnerType.DATASET_VERSION,
                owner_id=f"{dataset_id}:{dataset_version}",
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        return ExportAnalyticsSnapshotJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            table_uris={name: table.uri for name, table in tables.items()},
            row_counts=row_counts,
            scene_count=len(all_scene_records),
        )
