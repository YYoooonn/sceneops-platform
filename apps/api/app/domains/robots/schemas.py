from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_core.jobs.schemas.manifests import JobManifest
from sceneops_core.robots.schemas import (
    MissionRecord,
    RobotRecord,
    RobotRunRecord,
    RobotStateRecord,
)

# ── Robot ────────────────────────────────────────────────────────────────────


class CreateRobotRequest(SceneOpsBaseModel):
    robot_id: str
    name: str | None = None
    platform: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class RobotDetailResponse(SceneOpsBaseModel):
    robot: RobotRecord


class RobotListResponse(SceneOpsBaseModel):
    robots: list[RobotRecord]
    count: int


# ── RobotRun ─────────────────────────────────────────────────────────────────


class RegisterRobotRunRequest(SceneOpsBaseModel):
    """``POST /robot-runs:register`` -- the only way to create a RobotRun.
    ``manifest_uri`` names a RobotRunManifest written by the Recording
    Publisher; the REGISTER_ROBOT_RUN job verifies it and the recording it
    references before registering anything."""

    manifest_uri: str = Field(min_length=1)


class RegisterRobotRunResponse(SceneOpsBaseModel):
    job: JobManifest
    # None when an equivalent job already exists and was not re-dispatched
    # (job dedup by execution key; see JobService.create_job).
    execution: ExecutionDispatchResult | None = None


class RobotRunDetailResponse(SceneOpsBaseModel):
    robot_run: RobotRunRecord


class RobotRunListResponse(SceneOpsBaseModel):
    robot_runs: list[RobotRunRecord]
    count: int


# ── Mission (read-only via API — written by ingest_robot_states job) ────────


class MissionDetailResponse(SceneOpsBaseModel):
    mission: MissionRecord


class MissionListResponse(SceneOpsBaseModel):
    missions: list[MissionRecord]
    count: int


# ── RobotState (read-only via API — written by ingest_robot_states job) ─────


class RobotStateListResponse(SceneOpsBaseModel):
    robot_states: list[RobotStateRecord]
    count: int
