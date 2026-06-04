from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class SceneSegmentationStrategy(StrEnum):
    FIXED_WINDOW = "fixed_window"
    GAP_BASED = "gap_based"
    SOURCE_SCENE = "source_scene"
    MANUAL = "manual"


class SceneSegmentationConfig(SceneOpsBaseModel):
    strategy: SceneSegmentationStrategy = SceneSegmentationStrategy.SOURCE_SCENE

    window_seconds: float | None = None
    stride_seconds: float | None = None

    required_channels: list[str] = Field(
        default_factory=lambda: ["CAM_FRONT", "LIDAR_TOP"]
    )

    max_timestamp_gap_ms: int | None = 500
    min_frame_count: int = 2

    split_on_missing_required_channel: bool = True
    split_on_timestamp_gap: bool = False
    split_on_source_scene_boundary: bool = True

    metadata: JsonDict = Field(default_factory=dict)
