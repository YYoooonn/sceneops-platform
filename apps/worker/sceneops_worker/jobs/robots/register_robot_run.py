from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterRobotRunJobParams,
    RegisterRobotRunJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.robots.registration import register_robot_run


class RegisterRobotRunJobHandler(
    JobHandler[RegisterRobotRunJobParams, RegisterRobotRunJobResult]
):
    """REGISTER_ROBOT_RUN(manifest_uri): verifies a published
    RobotRunManifest and its recording, then registers both ArtifactRecords
    and the immutable RobotRunRecord in one transaction (ADR-007 §12.1).

    A standalone job, never a task of an ingestion pipeline. Retries
    converge: an identical manifest is a no-op (``created=False``); a
    different manifest for an already-registered run_id fails.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_ROBOT_RUN

    @property
    def params_model(self) -> type[RegisterRobotRunJobParams]:
        return RegisterRobotRunJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return dict(inputs.params)

    async def run(
        self,
        request: JobHandlerRequest[RegisterRobotRunJobParams],
    ) -> RegisterRobotRunJobResult:
        registration = await register_robot_run(
            context=request.context,
            manifest_uri=request.params.manifest_uri,
            job_id=request.job.job_id,
        )
        robot_run = registration.robot_run
        return RegisterRobotRunJobResult(
            run_id=robot_run.run_id,
            robot_id=robot_run.robot_id,
            recording_artifact_id=robot_run.recording_artifact_id,
            manifest_artifact_id=robot_run.manifest_artifact_id,
            manifest_checksum=robot_run.manifest_checksum,
            created=registration.created,
        )
