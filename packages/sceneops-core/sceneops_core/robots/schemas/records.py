from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import MissionStatus, RobotOperationState, RobotRunStatus, RobotStatus


class RobotRecord(SceneOpsBaseModel):
    robot_id: str
    name: str | None = None
    platform: str | None = None

    status: RobotStatus = RobotStatus.REGISTERED

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RobotRunRecord(SceneOpsBaseModel):
    """One physical recording session for a robot (maps to one rosbag2/MCAP file).

    Distinct from ``PipelineRun`` (a SceneOps-internal processing execution) — a
    RobotRun is the real-world execution that produces the raw data a pipeline
    later ingests. See docs/robot-data-model.md §5.
    """

    run_id: str
    robot_id: str

    status: RobotRunStatus = RobotRunStatus.RECORDING

    dataset_id: str | None = None
    dataset_version: str | None = None
    raw_log_id: str | None = None

    rosbag_uri: str | None = None
    mcap_uri: str | None = None

    started_at: datetime | None = None
    ended_at: datetime | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class MissionRecord(SceneOpsBaseModel):
    mission_id: str
    robot_id: str
    robot_run_id: str | None = None

    status: MissionStatus = MissionStatus.PENDING

    started_at: datetime | None = None
    ended_at: datetime | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RobotStateRecord(SceneOpsBaseModel):
    """Canonical robot runtime state sample (docs/robot-data-model.md §2).

    Field names/shapes intentionally mirror ``RawEgoPoseManifest``
    (sceneops_core.observations.schemas.frames) since ego_pose is a subset of
    this concept — translation/rotation use the same list[float] + rotation_format
    convention so a future merge is a narrowing, not a rewrite.
    """

    state_id: str
    robot_id: str
    robot_run_id: str | None = None
    mission_id: str | None = None
    scene_id: str | None = None

    timestamp_us: int

    position: list[float] | None = None
    orientation: list[float] | None = None
    rotation_format: str = "quaternion_wxyz"

    velocity: list[float] | None = None
    acceleration: list[float] | None = None

    steering: float | None = None
    throttle: float | None = None
    brake: float | None = None

    battery: float | None = None
    operation_state: RobotOperationState | None = None

    created_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
