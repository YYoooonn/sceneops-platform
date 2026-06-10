from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import SensorModality


class TimeRange(SceneOpsBaseModel):
    start_timestamp_us: int
    end_timestamp_us: int


class RawSensorFrameManifest(SceneOpsBaseModel):
    frame_id: str
    timestamp_us: int

    channel: str
    modality: SensorModality
    uri: str

    sequence_id: str | None = None
    sensor_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RawCalibrationManifest(SceneOpsBaseModel):
    calibration_id: str
    sensor_id: str

    channel: str | None = None
    modality: SensorModality | None = None

    translation: list[float] | None = None
    rotation: list[float] | None = None
    rotation_format: str = "quaternion_wxyz"

    camera_intrinsic: list[list[float]] | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RawEgoPoseManifest(SceneOpsBaseModel):
    ego_pose_id: str
    timestamp_us: int

    translation: list[float] | None = None
    rotation: list[float] | None = None
    rotation_format: str = "quaternion_wxyz"

    metadata: JsonDict = Field(default_factory=dict)
