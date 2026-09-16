from __future__ import annotations

from enum import StrEnum


class DatasetType(StrEnum):
    NUSCENES = "nuscenes"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"


class DatasetVersionStatus(StrEnum):
    """Generic, domain-agnostic DatasetVersion lifecycle (SceneOps V2 Request 05).

    Previously carried Scene-workflow-specific values (INGESTING/INGESTED/
    VALIDATING/PROFILING/READY/FAILED/DEPRECATED) — all removed. Scene
    processing progress is now represented by Pipeline/Job/Run execution
    records and Scene-domain artifacts (SceneVersionSummary), not by a
    generic DatasetVersion status. VALIDATING/PROFILING/FAILED/DEPRECATED had
    no writer anywhere in the codebase (dead even before this cleanup); the
    rest were exclusively Scene-workflow transitions.

    A dataset version's readiness for a specific downstream operation (e.g.
    detection prediction/evaluation) is now determined by explicit
    domain-scoped prerequisites — see
    predict_detection.py/evaluate_detection.py's
    ``_require_scene_dataset_ready`` — not by this field.
    """

    REGISTERED = "registered"


class DatasetManifestStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class DatasetIngestMode(StrEnum):
    UPSERT = "upsert"
    OVERWRITE = "overwrite"
    APPEND = "append"


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"
    UNASSIGNED = "unassigned"
