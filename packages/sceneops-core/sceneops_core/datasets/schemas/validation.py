from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class DatasetValidationScope(StrEnum):
    FULL = "full"
    SAMPLED = "sampled"
    SCENE = "scene"


class DatasetValidationStatus(StrEnum):
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"


class DatasetValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DatasetValidationCheckType(StrEnum):
    MANIFEST = "manifest"
    SCENE_INDEX = "scene_index"
    SCENE_MANIFEST_REF = "scene_manifest_ref"
    REQUIRED_SENSOR_CHANNELS = "required_sensor_channels"
    SENSOR_COVERAGE = "sensor_coverage"
    ANNOTATION_COVERAGE = "annotation_coverage"
    TIMESTAMP = "timestamp"
    ARTIFACT_REF = "artifact_ref"
    CUSTOM = "custom"


class DatasetValidationIssue(SceneOpsBaseModel):
    check_type: DatasetValidationCheckType
    severity: DatasetValidationSeverity

    code: str
    message: str

    scene_id: str | None = None
    sample_id: str | None = None
    channel: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DatasetValidationReport(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    status: DatasetValidationStatus = DatasetValidationStatus.READY
    should_block_pipeline: bool = False

    checked_scene_count: int = 0
    checked_sample_count: int = 0

    issues: list[DatasetValidationIssue] = Field(default_factory=list)

    summary: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
