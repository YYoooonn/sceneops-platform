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


class DatasetIngestMode(StrEnum):
    UPSERT = "upsert"
    OVERWRITE = "overwrite"
    APPEND = "append"
