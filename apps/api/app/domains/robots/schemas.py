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
    """Metadata-only RobotRun registration -- ``mcap_uri``/``rosbag_uri``
    are stored as-is, with no artifact verification (no upload, no
    checksum, no ArtifactRecord). This is NOT the canonical recording
    registration path: a RobotRun created this way cannot be used as a
    materialization source by Episode building (``build_episodes``
    requires a recording ArtifactRecord for any ``robot_run_id`` it
    resolves, see ``RobotRunNotMaterializedError``). Use it only for
    metadata attachment ahead of ``ingest_robot_states`` or similar
    read-mostly flows; to register a recording for Episode building, use
    ``sceneops-worker robots register-capture`` instead."""

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
