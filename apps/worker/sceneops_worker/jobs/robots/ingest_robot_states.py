from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    IngestRobotStatesJobParams,
    IngestRobotStatesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.observations.artifacts import ObservationArtifactStore
from sceneops_worker.robots.resolver import resolve_recording


class IngestRobotStatesJobHandler(
    JobHandler[IngestRobotStatesJobParams, IngestRobotStatesJobResult]
):
    """Reads robot-state and mission-status topics from a rosbag2/MCAP file
    and persists both.

    Ingests missions in the same pass rather than as a separate job type:
    they come from the same bag, so a second job would just mean opening and
    decoding the same file twice for no benefit.

    Not part of any named SceneOps pipeline (dataset ingestion pipelines are a
    separate concept from RobotRun — docs/architecture/data-model.md §5). Dispatched
    as a standalone Job. The recording is read only through the verified
    recording resolver (ADR-007 §12.4), keyed by ``robot_run_id``, so it
    works the same for every ArtifactStore backend.
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

        async with resolve_recording(
            robot_run_id=params.robot_run_id,
            robot_store=context.robot_store,
            artifact_record_store=context.artifact_record_store,
            artifact_store=context.artifact_store,
        ) as recording:
            # observation_store is required by RosbagAdapter's constructor but
            # is only used by build_raw_log() (scene frame manifests), not by
            # extract_robot_states() — unused on this code path.
            adapter = RosbagAdapter(
                source_store=context.raw_source_store,
                source_root_uri=str(recording.local_path),
                observation_store=ObservationArtifactStore(
                    artifact_store=context.artifact_store,
                    dataset_root_uri=context.settings.dataset_root_uri,
                ),
            )
            # The RobotRunRecord is authoritative for which robot produced
            # the recording.
            robot_id = recording.robot_id
            states = adapter.extract_robot_states(
                robot_id=robot_id,
                robot_run_id=params.robot_run_id,
            )
            missions = adapter.extract_missions(
                robot_id=robot_id,
                robot_run_id=params.robot_run_id,
            )

        saved_states = await context.robot_store.create_states(states)
        for mission in missions:
            await context.robot_store.upsert_mission(mission)

        return IngestRobotStatesJobResult(
            robot_id=robot_id,
            robot_run_id=params.robot_run_id,
            state_count=len(saved_states),
            mission_count=len(missions),
            start_timestamp_us=(saved_states[0].timestamp_us if saved_states else None),
            end_timestamp_us=(saved_states[-1].timestamp_us if saved_states else None),
        )
