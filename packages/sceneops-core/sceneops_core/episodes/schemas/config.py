from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class EpisodeSegmentationStrategy(StrEnum):
    MISSION_BOUNDARY = "mission_boundary"
    WHOLE_RUN = "whole_run"
    FIXED_WINDOW = "fixed_window"


class EpisodeSegmentationConfig(SceneOpsBaseModel):
    """Configuration for splitting one EpisodeSource into EpisodeWindows.

    MISSION_BOUNDARY is the default — it preserves the behavior EpisodeBuilder
    had before this config existed (SceneOps V2 Request 13): one Episode per
    dated Mission, falling back to WHOLE_RUN when no dated Mission exists.
    """

    strategy: EpisodeSegmentationStrategy = EpisodeSegmentationStrategy.MISSION_BOUNDARY

    fixed_window_duration_ms: int | None = None  # required for FIXED_WINDOW

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self) -> "EpisodeSegmentationConfig":
        if self.strategy == EpisodeSegmentationStrategy.FIXED_WINDOW:
            if (
                self.fixed_window_duration_ms is None
                or self.fixed_window_duration_ms <= 0
            ):
                raise ValueError(
                    "fixed_window_duration_ms must be positive when strategy is "
                    "fixed_window"
                )
        return self
