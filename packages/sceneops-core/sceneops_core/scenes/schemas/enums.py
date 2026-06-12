from __future__ import annotations

from enum import StrEnum


class SceneStatus(StrEnum):
    CREATED = "created"
    BUILT = "built"
    VALIDATING = "validating"
    VALIDATED = "validated"
    PROFILING = "profiling"
    PROFILED = "profiled"
    FAILED = "failed"
    DEPRECATED = "deprecated"


class SceneOriginType(StrEnum):
    REAL = "real"
    RECONSTRUCTED = "reconstructed"
    SIMULATED = "simulated"
    GENERATED = "generated"
    REOBSERVED = "reobserved"
    AUGMENTED = "augmented"


class SceneGenerationMethod(StrEnum):
    RAW_LOG = "raw_log"
    DATASET = "dataset"
    PERS = "pers"
    CARLA = "carla"
    ISAAC_SIM = "isaac_sim"
    WORLD_MODEL = "world_model"
    MANUAL_EDIT = "manual_edit"
    UNKNOWN = "unknown"


class SceneAssetKind(StrEnum):
    IMAGE = "image"
    POINT_CLOUD = "point_cloud"
    ANNOTATION = "annotation"
    CALIBRATION = "calibration"
    EGO_POSE = "ego_pose"
    MESH = "mesh"
    GAUSSIAN_SPLAT = "gaussian_splat"
    WORLD_STATE = "world_state"
    METADATA = "metadata"
    OTHER = "other"
