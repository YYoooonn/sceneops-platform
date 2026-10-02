from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import MissionStatus, RobotOperationState, RobotStatus


class RobotRecord(SceneOpsBaseModel):
    robot_id: str
    name: str | None = None
    platform: str | None = None

    status: RobotStatus = RobotStatus.REGISTERED

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RobotRunRecord(SceneOpsBaseModel):
    """Immutable, searchable projection of one finalized, published and
    verified robot recording (ADR-007 §10).

    Existence means the recording is finalized, durably published and
    verified -- there is no lifecycle status. Created only by
    ``REGISTER_ROBOT_RUN`` and never updated afterwards. Recording URI,
    checksum and size live only on the recording ArtifactRecord; the full
    source facts (channels, capture source) live only in the
    RobotRunManifest. Not a DatasetVersion member.
    """

    run_id: str
    robot_id: str

    started_at: datetime
    ended_at: datetime

    recording_format: str
    source_clock: str

    recording_artifact_id: str
    manifest_artifact_id: str
    manifest_checksum: str

    # DB insert time; not a manifest field.
    registered_at: datetime | None = None


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
    """Canonical robot runtime state sample (docs/architecture/data-model.md §5).

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
