from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .config import SceneSegmentationConfig


class SceneSegment(SceneOpsBaseModel):
    segment_id: str
    raw_log_id: str

    start_timestamp_us: int
    end_timestamp_us: int

    frame_ids: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)

    segmentation: SceneSegmentationConfig
    quality_summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class SceneSegmentIndex(SceneOpsBaseModel):
    raw_log_id: str
    dataset_id: str
    dataset_version: str

    segments: list[SceneSegment] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
