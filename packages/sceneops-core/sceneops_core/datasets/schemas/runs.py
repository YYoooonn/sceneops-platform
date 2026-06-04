from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType

from .profile import DatasetProfileScope
from .validation import DatasetValidationScope, DatasetValidationStatus


class DatasetValidationRunRecord(BaseRunRecord):
    type: RunType = RunType.DATASET_VALIDATION

    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    validation_report_uri: str | None = None

    scope: DatasetValidationScope = DatasetValidationScope.FULL
    max_samples: int | None = None

    validation_status: DatasetValidationStatus | None = None
    should_block_pipeline: bool = False

    checked_scene_count: int | None = None
    checked_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    summary: JsonDict = Field(default_factory=dict)


class DatasetProfileRunRecord(BaseRunRecord):
    type: RunType = RunType.DATASET_PROFILE

    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    profile_report_uri: str | None = None

    scope: DatasetProfileScope = DatasetProfileScope.FULL
    max_samples: int | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    frame_count: int | None = None
    annotation_count: int | None = None

    observed_channels: list[str] = Field(default_factory=list)
    sensor_coverage: dict[str, float] = Field(default_factory=dict)

    channel_summary: JsonDict = Field(default_factory=dict)
    annotation_summary: JsonDict = Field(default_factory=dict)
    timestamp_summary: JsonDict = Field(default_factory=dict)


class DatasetDistributionRunRecord(BaseRunRecord):
    type: RunType = RunType.DATASET_DISTRIBUTION

    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    distribution_report_uri: str | None = None

    group_by: list[str] = Field(default_factory=list)

    summary: JsonDict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DatasetExportRunRecord(BaseRunRecord):
    type: RunType = RunType.DATASET_EXPORT

    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    scenario_set_uri: str | None = None

    output_format: str = "sceneops"
    export_uri: str | None = None

    exported_scene_count: int = 0
    exported_sample_count: int = 0

    summary: JsonDict = Field(default_factory=dict)
