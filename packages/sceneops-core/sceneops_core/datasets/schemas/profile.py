from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class DatasetProfileScope(StrEnum):
    FULL = "full"
    SAMPLED = "sampled"
    SCENE = "scene"


class DatasetChannelProfile(SceneOpsBaseModel):
    channel: str

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    coverage_ratio: float | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DatasetProfileReport(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)
    sensor_coverage: dict[str, float] = Field(default_factory=dict)

    channel_profiles: list[DatasetChannelProfile] = Field(default_factory=list)

    annotation_count: int | None = None
    annotation_summary: JsonDict = Field(default_factory=dict)

    timestamp_summary: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
