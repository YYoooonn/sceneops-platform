from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from .enums import DatasetType, DatasetVersionStatus, SceneOriginType


class DatasetRecord(SceneOpsBaseModel):
    id: str
    name: str | None = None
    dataset_type: DatasetType | str = DatasetType.CUSTOM
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None


class DatasetVersionRecord(SceneOpsBaseModel):
    id: str

    dataset_id: str
    version: str

    dataset_type: DatasetType | str = DatasetType.CUSTOM
    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED

    origin_type: SceneOriginType = SceneOriginType.REAL

    source_uri: str | None = None
    manifest_uri: str | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    latest_validation_run_id: str | None = None
    validation_status: str | None = None
    should_block_pipeline: bool | None = None
    validation_report_uri: str | None = None

    validation_issue_count: int | None = None
    validation_error_count: int | None = None
    validation_warning_count: int | None = None

    missing_scene_count: int | None = None
    missing_sample_count: int | None = None
    missing_channel_count: int | None = None
    missing_artifact_count: int | None = None

    latest_profile_run_id: str | None = None
    profile_report_uri: str | None = None

    profiled_scene_count: int | None = None
    profiled_sample_count: int | None = None

    observed_channel_count: int | None = None
    observed_channels: list[str] | None = None

    missing_required_channel_count: int | None = None
    sensor_coverage_ratio: float | None = None

    empty_annotation_sample_count: int | None = None
    empty_annotation_sample_ratio: float | None = None

    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None
