from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from app.domains.scenes.schemas import SceneQualityResponse


class UpdateDatasetRequest(SceneOpsBaseModel):
    name: str | None = None
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class UpdateDatasetVersionRequest(SceneOpsBaseModel):
    """PATCH body — all fields optional, only provided values are applied."""

    status: DatasetVersionStatus | None = None
    required_channels: list[str] | None = None
    metadata: JsonDict | None = None


class CreateDatasetVersionBody(SceneOpsBaseModel):
    """POST body for creating a dataset version.

    dataset_id is intentionally omitted — it comes from the path parameter.
    """

    version: str
    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED
    required_channels: list[str] = Field(default_factory=list)
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
    keyframe_count: int = 0
    observation_count: int = 0


class DatasetSceneQualitySectionSummary(SceneOpsBaseModel):
    """Readiness buckets aggregated over all scenes."""

    ready_scene_count: int = 0
    warning_scene_count: int = 0
    blocked_scene_count: int = 0
    unknown_scene_count: int = 0
    observed_channels: list[str] = Field(default_factory=list)


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
    validation: DatasetValidationSummary = Field(
        default_factory=DatasetValidationSummary
    )
    profile: DatasetProfileSummary = Field(default_factory=DatasetProfileSummary)


# ── Dataset scene quality list + aggregate response ───────────────────────────


class DatasetSceneQualityAggregateSummary(SceneOpsBaseModel):
    scene_count: int = 0
    ready_scene_count: int = 0
    warning_scene_count: int = 0
    blocked_scene_count: int = 0
    unknown_scene_count: int = 0

    total_keyframe_count: int = 0
    total_observation_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)


class DatasetSceneQualityListResponse(SceneOpsBaseModel):
    dataset_id: str
    version: str
    count: int
    limit: int
    offset: int
    summary: DatasetSceneQualityAggregateSummary
    scenes: list[SceneQualityResponse]
