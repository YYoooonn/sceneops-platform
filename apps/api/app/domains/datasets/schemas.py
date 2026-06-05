from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas.enums import DatasetType, DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.validation import DatasetValidationStatus


class UpdateDatasetRequest(SceneOpsBaseModel):
    name: str | None = None
    description: str | None = None
    type: DatasetType = DatasetType.CUSTOM
    metadata: JsonDict = Field(default_factory=dict)


class UpdateDatasetVersionRequest(SceneOpsBaseModel):
    """PATCH body — all fields optional, only provided values are applied."""

    status: DatasetVersionStatus | None = None
    manifest_uri: str | None = None
    scene_count: int | None = None
    sample_count: int | None = None
    frame_count: int | None = None
    channels: list[str] | None = None
    metadata: JsonDict | None = None


class CreateDatasetVersionBody(SceneOpsBaseModel):
    """POST body for creating a dataset version.

    dataset_id is intentionally omitted — it comes from the path parameter.
    """

    version: str
    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED
    manifest_uri: str | None = None
    source_dataset_id: str | None = None
    source_dataset_version: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class DatasetDetailResponse(SceneOpsBaseModel):
    dataset: DatasetRecord


class DatasetListResponse(SceneOpsBaseModel):
    datasets: list[DatasetRecord]
    count: int


class DatasetVersionDetailResponse(SceneOpsBaseModel):
    version: DatasetVersionRecord


class DatasetVersionListResponse(SceneOpsBaseModel):
    versions: list[DatasetVersionRecord]
    count: int


class DatasetVersionQualityResponse(SceneOpsBaseModel):
    dataset_id: str
    version: str
    latest_validation_run_id: str | None = None
    validation_status: DatasetValidationStatus | None = None
    should_block_pipeline: bool | None = None
    validation_report_uri: str | None = None
    latest_profile_run_id: str | None = None
    profile_report_uri: str | None = None
    latest_distribution_run_id: str | None = None
    distribution_report_uri: str | None = None
