from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import SensorModality

from .enums import SensorFrameRole


class TimeRange(SceneOpsBaseModel):
    start_timestamp_us: int
    end_timestamp_us: int


class RawSensorFrameManifest(SceneOpsBaseModel):
    frame_id: str
    timestamp_us: int

    channel: str
    modality: SensorModality = SensorModality.UNKNOWN
    role: SensorFrameRole = SensorFrameRole.UNKNOWN

    uri: str

    source_sample_id: str | None = None
    source_scene_id: str | None = None
    ego_pose_ref: str | None = None
    calibration_ref: str | None = None
    annotation_refs: list[str] = Field(default_factory=list)

    # Generic raw-log source identifiers
    source_sequence_id: str | None = None
    source_frame_id: str | None = None
    source_sensor_id: str | None = None
    duration_us: int | None = None

    metadata: JsonDict = Field(default_factory=dict)
