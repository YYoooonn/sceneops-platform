from __future__ import annotations

from enum import StrEnum


class SensorModality(StrEnum):
    CAMERA = "camera"
    LIDAR = "lidar"
    RADAR = "radar"
    EGO_POSE = "ego_pose"
    CALIBRATION = "calibration"
    ANNOTATION = "annotation"
    UNKNOWN = "unknown"
