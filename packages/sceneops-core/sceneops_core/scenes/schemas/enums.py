from __future__ import annotations

from enum import StrEnum


class SceneStatus(StrEnum):
    """Scene build/validation/profile lifecycle. Unlike EpisodeStatus, Scene's
    architecture does fold validation/profile outcomes into this field
    (BUILT -> VALIDATED/FAILED -> PROFILED) — see validate_scene.py /
    profile_scene.py. VALIDATING/PROFILING (transitional "in progress"
    states) and DEPRECATED (a soft-delete marker) were removed in
    Stabilization Request 5: zero write sites, zero persisted rows, and
    in-progress signaling is already covered by
    SceneValidationRunRecord/SceneProfileRunRecord's own RunStatus.RUNNING —
    the same reasoning that already removed DatasetVersionStatus's
    equivalent transitional values (see datasets/schemas/enums.py)."""

    CREATED = "created"
    BUILT = "built"
    VALIDATED = "validated"
    PROFILED = "profiled"
    FAILED = "failed"


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
