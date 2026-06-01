from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict


class DatasetValidationStatus(StrEnum):
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"


class DatasetValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DatasetValidationScope(StrEnum):
    FULL = "full"
    SAMPLED = "sampled"


class DatasetValidationCheckType(StrEnum):
    MANIFEST = "manifest"
    SCENE_INDEX = "scene_index"
    SCENE_MANIFEST = "scene_manifest"
    SAMPLE_MANIFEST = "sample_manifest"
    REQUIRED_SENSOR_CHANNELS = "required_sensor_channels"
    SENSOR_ARTIFACT = "sensor_artifact"
    ANNOTATION = "annotation"
    CALIBRATION = "calibration"
    TIMESTAMP = "timestamp"


class DatasetValidationIssue(SceneOpsBaseModel):
    check_type: DatasetValidationCheckType
    severity: DatasetValidationSeverity
    code: str
    message: str

    scene_id: str | None = None
    sample_id: str | None = None
    channel: str | None = None
    uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DatasetValidationSummary(SceneOpsBaseModel):
    scene_count: int = 0
    sample_count: int = 0
    annotation_count: int = 0

    validated_scene_count: int = 0
    validated_sample_count: int = 0

    issue_count: int = 0
    error_count: int = 0
    warning_count: int = 0

    missing_scene_count: int = 0
    missing_sample_count: int = 0
    missing_channel_count: int = 0
    missing_artifact_count: int = 0


class DatasetValidationDecision(SceneOpsBaseModel):
    status: DatasetValidationStatus
    should_block_pipeline: bool
    reason: str | None = None


class DatasetValidationReport(SceneOpsBaseModel):
    schema_version: str = "1.0"

    validation_run_id: str
    job_id: str | None = None

    dataset_id: str
    dataset_version: str
    dataset_manifest_uri: str

    status: DatasetValidationStatus
    scope: DatasetValidationScope
    max_samples: int | None = None

    should_block_pipeline: bool = False
    decision_reason: str | None = None

    summary: DatasetValidationSummary
    issues: list[DatasetValidationIssue] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
