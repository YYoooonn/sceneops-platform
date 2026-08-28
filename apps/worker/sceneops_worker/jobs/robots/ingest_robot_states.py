from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    IngestRobotStatesJobParams,
    IngestRobotStatesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.robots.schemas import RobotRunStatus
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.observations.artifacts import ObservationArtifactStore


class IngestRobotStatesJobHandler(
    JobHandler[IngestRobotStatesJobParams, IngestRobotStatesJobResult]
):
    """Reads robot-state topics from a rosbag2/MCAP file and persists them.

    Not part of any named SceneOps pipeline (dataset ingestion pipelines are a
    separate concept from RobotRun — docs/robot-data-model.md §5). Dispatched
    as a standalone Job, typically against a RobotRun that already has
    mcap_uri/rosbag_uri set by whatever recorded it.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.INGEST_ROBOT_STATES

    @property
    def params_model(self) -> type[IngestRobotStatesJobParams]:
        return IngestRobotStatesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return dict(inputs.params)

    async def run(
        self,
        request: JobHandlerRequest[IngestRobotStatesJobParams],
    ) -> IngestRobotStatesJobResult:
        params = request.params
        context = request.context

        robot = await context.robot_store.get_robot(params.robot_id)
        if robot is None:
            raise ValueError(f"Robot not found: {params.robot_id}")

        robot_run = None
        mcap_uri = params.mcap_uri
        if params.robot_run_id is not None:
            robot_run = await context.robot_store.get_run(params.robot_run_id)
            if robot_run is None:
                raise ValueError(f"RobotRun not found: {params.robot_run_id}")
            mcap_uri = mcap_uri or robot_run.mcap_uri or robot_run.rosbag_uri

        if not mcap_uri:
            raise ValueError(
                "ingest_robot_states requires mcap_uri, or a robot_run_id whose "
                "RobotRun has mcap_uri/rosbag_uri set."
            )

        # observation_store is required by RosbagAdapter's constructor but is
        # only used by build_raw_log() (scene frame manifests), not by
        # extract_robot_states() — unused on this code path.
        adapter = RosbagAdapter(
            source_store=context.raw_source_store,
            source_root_uri=mcap_uri,
            observation_store=ObservationArtifactStore(
                artifact_store=context.artifact_store,
                dataset_root_uri=context.settings.dataset_root_uri,
            ),
        )

        states = adapter.extract_robot_states(
            robot_id=params.robot_id,
            robot_run_id=params.robot_run_id,
        )
        saved_states = await context.robot_store.create_states(states)

        if robot_run is not None:
            await context.robot_store.save_run(
                robot_run.model_copy(update={"status": RobotRunStatus.INGESTED})
            )

        return IngestRobotStatesJobResult(
            robot_id=params.robot_id,
            robot_run_id=params.robot_run_id,
            state_count=len(saved_states),
            start_timestamp_us=(saved_states[0].timestamp_us if saved_states else None),
            end_timestamp_us=(saved_states[-1].timestamp_us if saved_states else None),
        )
