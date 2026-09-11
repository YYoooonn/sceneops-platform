from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
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


class CreateRobotRunRequest(SceneOpsBaseModel):
    run_id: str
    robot_id: str
    mcap_uri: str | None = None
    rosbag_uri: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


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
