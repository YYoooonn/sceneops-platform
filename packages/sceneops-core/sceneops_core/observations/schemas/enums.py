from __future__ import annotations

from enum import StrEnum


class RawLogSourceFormat(StrEnum):
    NUSCENES = "nuscenes"
    ROSBAG = "rosbag"
    FOLDER = "folder"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"


class SensorFrameRole(StrEnum):
    IMAGE = "image"
    POINT_CLOUD = "point_cloud"
    RADAR = "radar"
    EGO_POSE = "ego_pose"
    CALIBRATION = "calibration"
    ANNOTATION = "annotation"
    UNKNOWN = "unknown"
