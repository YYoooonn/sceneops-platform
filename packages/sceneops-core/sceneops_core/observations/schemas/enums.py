from __future__ import annotations

from enum import StrEnum


class RawLogSourceFormat(StrEnum):
    NUSCENES = "nuscenes"
    ROSBAG = "rosbag"
    FOLDER = "folder"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"


class RawLogSourceType(StrEnum):
    REAL_ROBOT_LOG = "real_robot_log"
    NUSCENES_RAW_LOG_MOCK = "nuscenes_raw_log_mock"
    GENERIC_FILE_SEQUENCE = "generic_file_sequence"
    SIMULATOR_LOG = "simulator_log"


class SensorFrameRole(StrEnum):
    IMAGE = "image"
    POINT_CLOUD = "point_cloud"
    RADAR = "radar"
    EGO_POSE = "ego_pose"
    CALIBRATION = "calibration"
    ANNOTATION = "annotation"
    UNKNOWN = "unknown"
