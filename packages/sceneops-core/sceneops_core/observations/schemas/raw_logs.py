from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import SensorModality

from .enums import RawLogSourceFormat, RawLogSourceType
from .frames import (
    RawSensorFrameManifest,
    TimeRange,
    RawCalibrationManifest,
    RawEgoPoseManifest,
)


class RawLogManifest(SceneOpsBaseModel):
    raw_log_id: str

    dataset_id: str
    dataset_version: str
    dataset_type: str

    source_format: RawLogSourceFormat
    source_type: RawLogSourceType | None = None
    root_uri: str

    channels: list[str] = Field(default_factory=list)
    modalities: list[SensorModality] = Field(default_factory=list)

    frame_count: int = 0
    calibration_count: int = 0
    ego_pose_count: int = 0

    sequence_count: int = 0
    time_range: TimeRange | None = None

    frame_index_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RawLogFrameIndex(SceneOpsBaseModel):
    raw_log_id: str
    dataset_id: str
    dataset_version: str

    frames: list[RawSensorFrameManifest] = Field(default_factory=list)
    calibrations: list[RawCalibrationManifest] = Field(default_factory=list)
    ego_poses: list[RawEgoPoseManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
