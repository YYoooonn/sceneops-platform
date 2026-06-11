from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas.enums import DatasetType, DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from app.domains.scenes.schemas import SceneQualityResponse


class UpdateDatasetRequest(SceneOpsBaseModel):
    name: str | None = None
    description: str | None = None
    type: DatasetType = DatasetType.CUSTOM
    metadata: JsonDict = Field(default_factory=dict)


class UpdateDatasetVersionRequest(SceneOpsBaseModel):
    """PATCH body — all fields optional, only provided values are applied."""

    status: DatasetVersionStatus | None = None
    manifest_uri: str | None = None
    raw_source_root_uri: str | None = None
    scene_count: int | None = None
    sample_count: int | None = None
    frame_count: int | None = None
    channels: list[str] | None = None
    required_channels: list[str] | None = None
    metadata: JsonDict | None = None


class CreateDatasetVersionBody(SceneOpsBaseModel):
    """POST body for creating a dataset version.

    dataset_id is intentionally omitted — it comes from the path parameter.
    """

    version: str
    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED
    manifest_uri: str | None = None
    raw_source_root_uri: str | None = None
    required_channels: list[str] = Field(default_factory=list)
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


# ── Dataset quality summary response (scene-aggregate based) ──────────────────


class DatasetQualityReadiness(StrEnum):
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class DatasetVersionQualityCounts(SceneOpsBaseModel):
    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0
    annotation_count: int = 0
    ground_truth_scene_count: int = 0
    selectable_scene_count: int = 0


class DatasetSceneQualitySectionSummary(SceneOpsBaseModel):
    """Readiness and selectability buckets aggregated over all scenes."""

    ready_scene_count: int = 0
    warning_scene_count: int = 0
    blocked_scene_count: int = 0
    unknown_scene_count: int = 0
    selectable_for_detection_count: int = 0
    non_selectable_for_detection_count: int = 0
    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
    observed_channels: list[str] = Field(default_factory=list)


class DatasetGroundTruthSummary(SceneOpsBaseModel):
    has_ground_truth: bool = False
    ground_truth_scene_count: int = 0
    annotated_scene_count: int = 0
    annotation_count: int = 0
    ground_truth_coverage_ratio: float = 0.0


class DatasetValidationSummary(SceneOpsBaseModel):
    """Per-scene validation readiness aggregate — not a single run record."""

    ready_scene_count: int = 0
    warning_scene_count: int = 0
    blocked_scene_count: int = 0
    unknown_scene_count: int = 0


class DatasetProfileSummary(SceneOpsBaseModel):
    """Observed channels union across all scene profile runs."""

    observed_channels: list[str] = Field(default_factory=list)


class DatasetVersionQualityResponse(SceneOpsBaseModel):
    """Compact operator-facing dataset quality summary derived from scene aggregate."""

    dataset_id: str
    version: str
    status: str
    readiness: DatasetQualityReadiness = DatasetQualityReadiness.UNKNOWN

    counts: DatasetVersionQualityCounts = Field(
        default_factory=DatasetVersionQualityCounts
    )
    scene_quality: DatasetSceneQualitySectionSummary = Field(
        default_factory=DatasetSceneQualitySectionSummary
    )
    ground_truth: DatasetGroundTruthSummary = Field(
        default_factory=DatasetGroundTruthSummary
    )
    validation: DatasetValidationSummary = Field(
        default_factory=DatasetValidationSummary
    )
    profile: DatasetProfileSummary = Field(default_factory=DatasetProfileSummary)
    manifest_uri: str | None = None


# ── Dataset scene quality list + aggregate response ───────────────────────────


class DatasetSceneQualityAggregateSummary(SceneOpsBaseModel):
    scene_count: int = 0
    ready_scene_count: int = 0
    warning_scene_count: int = 0
    blocked_scene_count: int = 0
    unknown_scene_count: int = 0

    selectable_for_detection_count: int = 0
    non_selectable_for_detection_count: int = 0

    ground_truth_scene_count: int = 0
    annotated_scene_count: int = 0
    total_sample_count: int = 0
    total_frame_count: int = 0
    total_annotation_count: int = 0

    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
    observed_channels: list[str] = Field(default_factory=list)


class DatasetSceneQualityListResponse(SceneOpsBaseModel):
    dataset_id: str
    version: str
    count: int
    limit: int
    offset: int
    summary: DatasetSceneQualityAggregateSummary
    scenes: list[SceneQualityResponse]
