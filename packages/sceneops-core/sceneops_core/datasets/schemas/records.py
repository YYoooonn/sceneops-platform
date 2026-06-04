from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import DatasetStatus, DatasetType, DatasetVersionStatus
from .validation import DatasetValidationStatus


class DatasetRecord(SceneOpsBaseModel):
    dataset_id: str
    name: str | None = None
    description: str | None = None

    type: DatasetType = DatasetType.CUSTOM
    status: DatasetStatus = DatasetStatus.CREATED

    default_version: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DatasetVersionRecord(SceneOpsBaseModel):
    dataset_id: str
    version: str

    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED

    manifest_uri: str | None = None

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    channels: list[str] = Field(default_factory=list)

    source_dataset_id: str | None = None
    source_dataset_version: str | None = None

    # Latest dataset quality cache.
    # Source of truth is dataset_run_records.
    latest_validation_run_id: str | None = None
    validation_status: DatasetValidationStatus | None = None
    should_block_pipeline: bool | None = None
    validation_report_uri: str | None = None

    latest_profile_run_id: str | None = None
    profile_report_uri: str | None = None

    latest_distribution_run_id: str | None = None
    distribution_report_uri: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
