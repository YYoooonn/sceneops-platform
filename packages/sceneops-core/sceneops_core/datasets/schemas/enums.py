from __future__ import annotations

from enum import StrEnum


class DatasetType(StrEnum):
    NUSCENES = "nuscenes"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"


class DatasetManifestStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class DatasetVersionStatus(StrEnum):
    REGISTERED = "registered"
    INGESTING = "ingesting"
    INGESTED = "ingested"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    DEPRECATED = "deprecated"


class SensorModality(StrEnum):
    CAMERA = "camera"
    LIDAR = "lidar"
    RADAR = "radar"
    UNKNOWN = "unknown"


class DatasetIngestMode(StrEnum):
    UPSERT = "upsert"
    OVERWRITE = "overwrite"
    APPEND = "append"


class RawLogSourceFormat(StrEnum):
    NUSCENES = "nuscenes"
    ROSBAG = "rosbag"
    FOLDER = "folder"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"


class SceneBuildPolicyType(StrEnum):
    EXISTING_DATASET_SCENE = "existing_dataset_scene"
    FIXED_WINDOW = "fixed_window"
    GAP_BASED = "gap_based"
    EVENT_BASED = "event_based"


class SensorFrameRole(StrEnum):
    IMAGE = "image"
    POINT_CLOUD = "point_cloud"
    RADAR = "radar"
    EGO_POSE = "ego_pose"
    CALIBRATION = "calibration"
    ANNOTATION = "annotation"
    UNKNOWN = "unknown"
