from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors.enums import SensorModality


class SensorCalibrationManifest(SceneOpsBaseModel):
    """Static sensor calibration (extrinsic + intrinsic) for one sensor.

    Sensor calibration is not time-varying — it belongs to the sensor rig,
    not to individual sample_data records.  A scene registry deduplicates these
    by calibration_id so each unique rig configuration is stored once.
    """

    calibration_id: str
    sensor_id: str

    channel: str | None = None
    modality: SensorModality | None = None

    translation: list[float] | None = None
    rotation: list[float] | None = None
    rotation_format: str = "quaternion_wxyz"

    camera_intrinsic: list[list[float]] | None = None

    metadata: JsonDict = Field(default_factory=dict)


class EgoPoseManifest(SceneOpsBaseModel):
    """Vehicle ego-pose at a specific timestamp.

    timestamp_us must come from ego_pose["timestamp"] in nuScenes —
    not from sample["timestamp"] or sample_data["timestamp"].
    """

    ego_pose_id: str
    timestamp_us: int | None = None

    translation: list[float] | None = None
    rotation: list[float] | None = None
    rotation_format: str = "quaternion_wxyz"

    metadata: JsonDict = Field(default_factory=dict)


class ImageMetadataManifest(SceneOpsBaseModel):
    """Image dimensions and format metadata for a camera sensor frame."""

    width: int | None = None
    height: int | None = None
    fileformat: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
