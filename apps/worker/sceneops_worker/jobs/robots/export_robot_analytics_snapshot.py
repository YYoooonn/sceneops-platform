from __future__ import annotations

from sceneops_analytics import (
    ROBOT_TABLE_BUILDERS,
    build_missions_table,
    build_robot_telemetry_table,
)
from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    ExportRobotAnalyticsSnapshotJobParams,
    ExportRobotAnalyticsSnapshotJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest

# Row-count cap for a single robot_run's analytics export. RobotState is a
# high-frequency time series (see docs/robot-data-model.md §6) — this bound
# just needs to comfortably exceed one run's message count (~3k for a
# ~6s/10x-rate nuScenes CAN replay); revisit if runs get much longer.
_MAX_ROWS_PER_TABLE = 200_000


class ExportRobotAnalyticsSnapshotJobHandler(
    JobHandler[
        ExportRobotAnalyticsSnapshotJobParams, ExportRobotAnalyticsSnapshotJobResult
    ]
):
    """Exports RobotState/Mission rows for one RobotRun to Parquet.

    Mirrors ExportAnalyticsSnapshotJobHandler (dataset-scoped) but reads from
    RobotStore instead of scene_store/scene_artifact_store, and writes under
    AnalyticsTableWriter's robot-run scope instead of the dataset scope —
    Robot/RobotRun is a separate domain from Dataset/DatasetVersion
    (docs/robot-data-model.md §5).
    """

    @property
    def job_type(self) -> JobType:
        return JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT

    @property
    def params_model(self) -> type[ExportRobotAnalyticsSnapshotJobParams]:
        return ExportRobotAnalyticsSnapshotJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return dict(inputs.params)

    async def run(
        self,
        request: JobHandlerRequest[ExportRobotAnalyticsSnapshotJobParams],
    ) -> ExportRobotAnalyticsSnapshotJobResult:
        job = request.job
        params = request.params
        context = request.context

        robot_run_id = params.robot_run_id
        robot_run = await context.robot_store.get_run(robot_run_id)
        if robot_run is None:
            raise ValueError(
                f"export_robot_analytics_snapshot: RobotRun not found: {robot_run_id!r}"
            )

        requested_tables = (
            set(params.tables) if params.tables else set(ROBOT_TABLE_BUILDERS)
        )

        table_uris: dict[str, str] = {}
        row_counts: dict[str, int] = {}

        if "robot_telemetry" in requested_tables:
            states = await context.robot_store.list_states(
                robot_run_id=robot_run_id, limit=_MAX_ROWS_PER_TABLE
            )
            df = build_robot_telemetry_table(states)
            uri = await context.analytics_writer.write_robot_run_table(
                "robot_telemetry", df, robot_run_id=robot_run_id
            )
            table_uris["robot_telemetry"] = uri
            row_counts["robot_telemetry"] = df.height

        if "missions" in requested_tables:
            missions = await context.robot_store.list_missions(
                robot_run_id=robot_run_id, limit=_MAX_ROWS_PER_TABLE
            )
            df = build_missions_table(missions)
            uri = await context.analytics_writer.write_robot_run_table(
                "missions", df, robot_run_id=robot_run_id
            )
            table_uris["missions"] = uri
            row_counts["missions"] = df.height

        for table_name, uri in table_uris.items():
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.ANALYTICS_TABLE,
                    uri=uri,
                    media_type="application/vnd.apache.parquet",
                ),
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=robot_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        return ExportRobotAnalyticsSnapshotJobResult(
            robot_run_id=robot_run_id,
            table_uris=table_uris,
            row_counts=row_counts,
        )
