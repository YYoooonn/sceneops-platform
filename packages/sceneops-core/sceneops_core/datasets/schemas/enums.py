from __future__ import annotations

from enum import StrEnum


class DatasetType(StrEnum):
    NUSCENES = "nuscenes"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"


class DatasetStatus(StrEnum):
    CREATED = "created"
    REGISTERED = "registered"
    INGESTING = "ingesting"
    INGESTED = "ingested"
    VALIDATING = "validating"
    PROFILING = "profiling"
    READY = "ready"
    FAILED = "failed"
    DEPRECATED = "deprecated"


class DatasetVersionStatus(StrEnum):
    REGISTERED = "registered"
    INGESTING = "ingesting"
    INGESTED = "ingested"
    VALIDATING = "validating"
    PROFILING = "profiling"
    READY = "ready"
    FAILED = "failed"
    DEPRECATED = "deprecated"


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
