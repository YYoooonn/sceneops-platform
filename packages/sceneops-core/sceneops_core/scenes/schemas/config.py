from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class SceneSegmentationStrategy(StrEnum):
    SEQUENCE = "sequence"
    FIXED_WINDOW = "fixed_window"
    GAP_BASED = "gap_based"
    MANUAL = "manual"


class SceneSegmentationConfig(SceneOpsBaseModel):
    """Configuration for raw-frame segmentation into scene segments."""

    strategy: SceneSegmentationStrategy = SceneSegmentationStrategy.FIXED_WINDOW

    # Sliding-window / stride — reserved for future strategies.
    window_seconds: float | None = None
    stride_seconds: float | None = None

    # Informational: not enforced by current segmentation implementations.
    required_channels: list[str] = Field(
        default_factory=lambda: ["CAM_FRONT", "LIDAR_TOP"]
    )

    # Fixed-window segmentation: duration of each time window.
    fixed_window_duration_ms: int | None = 10000

    # Gap-based segmentation: max gap between consecutive frames before splitting.
    max_timestamp_gap_ms: int | None = 500
    min_frame_count: int = 2

    # Reserved flags — not yet wired into segmentation logic.
    split_on_missing_required_channel: bool = True
    split_on_timestamp_gap: bool = False
    split_on_sequence_boundary: bool = True

    metadata: JsonDict = Field(default_factory=dict)
