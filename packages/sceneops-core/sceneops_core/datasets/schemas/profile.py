from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict


class DatasetProfileScope(StrEnum):
    FULL = "full"
    SAMPLED = "sampled"


class DatasetChannelProfile(SceneOpsBaseModel):
    channel: str
    modality: str | None = None

    sample_count: int = 0
    missing_count: int = 0
    coverage_ratio: float = 0.0


class DatasetSceneProfile(SceneOpsBaseModel):
    scene_id: str

    sample_count: int = 0
    annotation_count: int = 0

    channel_counts: dict[str, int] = Field(default_factory=dict)


class DatasetAnnotationProfile(SceneOpsBaseModel):
    total_count: int = 0
    class_distribution: dict[str, int] = Field(default_factory=dict)

    empty_sample_count: int = 0
    empty_sample_ratio: float = 0.0


class DatasetProfileSummary(SceneOpsBaseModel):
    scene_count: int = 0
    sample_count: int = 0
    annotation_count: int = 0

    profiled_scene_count: int = 0
    profiled_sample_count: int = 0

    observed_channel_count: int = 0

    missing_required_channel_count: int = 0
    sensor_coverage_ratio: float = 0.0

    empty_annotation_sample_count: int = 0
    empty_annotation_sample_ratio: float = 0.0


class DatasetProfileReport(SceneOpsBaseModel):
    schema_version: str = "1.0"

    profile_run_id: str
    job_id: str | None = None

    dataset_id: str
    dataset_version: str
    dataset_manifest_uri: str

    scope: DatasetProfileScope = DatasetProfileScope.FULL
    max_samples: int | None = None

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    required_channels: list[str] = Field(default_factory=list)
    observed_channels: list[str] = Field(default_factory=list)

    summary: DatasetProfileSummary

    channels: list[DatasetChannelProfile] = Field(default_factory=list)
    scenes: list[DatasetSceneProfile] = Field(default_factory=list)
    annotations: DatasetAnnotationProfile = Field(default_factory=DatasetAnnotationProfile)

    metadata: JsonDict = Field(default_factory=dict)
