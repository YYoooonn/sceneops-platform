from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class SceneSegmentationStrategy(StrEnum):
    SEQUENCE = "sequence"
    GAP_BASED = "gap_based"
    FIXED_WINDOW = "fixed_window"


class MissingSequencePolicy(StrEnum):
    DEFAULT_SEGMENT = "default_segment"
    DROP = "drop"


class SceneSegmentationConfig(SceneOpsBaseModel):
    """Configuration for raw-frame segmentation into scene segments."""

    strategy: SceneSegmentationStrategy = SceneSegmentationStrategy.FIXED_WINDOW

    respect_sequence_id: bool = True

    missing_sequence_policy: MissingSequencePolicy = (
        MissingSequencePolicy.DEFAULT_SEGMENT
    )
    default_sequence_id: str = "default"

    min_frame_count: int = 1
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    max_frame_count: int | None = None

    max_timestamp_gap_ms: int | None = 500  # gap_based
    fixed_window_duration_ms: int | None = 10000  # fixed_window
    fixed_window_stride_ms: int | None = None

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self) -> "SceneSegmentationConfig":
        if self.min_frame_count < 1:
            raise ValueError("min_frame_count must be >= 1")

        if self.min_duration_ms is not None and self.min_duration_ms < 0:
            raise ValueError("min_duration_ms must be >= 0 when provided")

        if self.max_duration_ms is not None and self.max_duration_ms <= 0:
            raise ValueError("max_duration_ms must be > 0 when provided")

        if (
            self.min_duration_ms is not None
            and self.max_duration_ms is not None
            and self.min_duration_ms > self.max_duration_ms
        ):
            raise ValueError("min_duration_ms cannot be greater than max_duration_ms")

        if self.max_frame_count is not None and self.max_frame_count < 1:
            raise ValueError("max_frame_count must be >= 1 when provided")

        if self.strategy == SceneSegmentationStrategy.GAP_BASED:
            if self.max_timestamp_gap_ms is None or self.max_timestamp_gap_ms <= 0:
                raise ValueError(
                    "max_timestamp_gap_ms must be positive when strategy is gap_based"
                )

        if self.strategy == SceneSegmentationStrategy.FIXED_WINDOW:
            if (
                self.fixed_window_duration_ms is None
                or self.fixed_window_duration_ms <= 0
            ):
                raise ValueError(
                    "fixed_window_duration_ms must be positive when strategy is "
                    "fixed_window"
                )

            if (
                self.fixed_window_stride_ms is not None
                and self.fixed_window_stride_ms <= 0
            ):
                raise ValueError(
                    "fixed_window_stride_ms must be positive when provided"
                )

        return self
