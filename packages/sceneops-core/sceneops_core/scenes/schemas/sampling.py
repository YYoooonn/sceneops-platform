from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class SampleGroupingStrategy(StrEnum):
    ANCHOR_CHANNEL = "anchor_channel"
    FIXED_INTERVAL = "fixed_interval"


class FrameAssociationStrategy(StrEnum):
    NEAREST = "nearest"
    PREVIOUS = "previous"
    NEXT = "next"


class EgoPoseResolveStrategy(StrEnum):
    NEAREST = "nearest"
    EXACT = "exact"
    INTERPOLATE = "interpolate"


class SampleGroupingConfig(SceneOpsBaseModel):
    """Configuration for grouping raw sensor frames into scene samples."""

    # SAMPLE TIMESTAMP SELECTION
    strategy: SampleGroupingStrategy = SampleGroupingStrategy.ANCHOR_CHANNEL
    anchor_channel: str = "LIDAR_TOP"  # anchor_channel
    sample_interval_ms: int | None = None  # fixed_interval
    # Optional downsampling
    every_nth_anchor: int = 1
    max_samples: int | None = None
    # Prevent over-dense samples when anchor frames are too close.
    min_sample_gap_ms: int | None = None

    # FRAME ASSOCIATION
    required_channels: list[str] = Field(default_factory=list)

    association_strategy: FrameAssociationStrategy = FrameAssociationStrategy.NEAREST
    association_tolerance_ms: int = 100

    # Whether the same raw frame may be associated to multiple samples.
    allow_frame_reuse: bool = True
    drop_empty_samples: bool = True

    drop_samples_missing_required_channels: bool = False

    # CALIBRATION / EGO POSE
    allow_missing_calibration: bool = True

    ego_pose_strategy: EgoPoseResolveStrategy = EgoPoseResolveStrategy.NEAREST
    ego_pose_tolerance_ms: int = 100
    allow_missing_ego_pose: bool = True

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self) -> "SampleGroupingConfig":
        if self.every_nth_anchor < 1:
            raise ValueError("every_nth_anchor must be >= 1")

        if self.max_samples is not None and self.max_samples < 1:
            raise ValueError("max_samples must be >= 1 when provided")

        if self.association_tolerance_ms < 0:
            raise ValueError("association_tolerance_ms must be >= 0")

        if self.ego_pose_tolerance_ms < 0:
            raise ValueError("ego_pose_tolerance_ms must be >= 0")

        if self.strategy == SampleGroupingStrategy.FIXED_INTERVAL:
            if self.sample_interval_ms is None or self.sample_interval_ms <= 0:
                raise ValueError(
                    "sample_interval_ms must be a positive integer when "
                    "strategy == fixed_interval"
                )

        return self

    # strategy: SampleGroupingStrategy = SampleGroupingStrategy.TIME_BUCKET

    # sample_time_window_ms: float | None = 500.0
    # reference_channel: str | None = None

    # sync_policy: SensorSyncPolicy = SensorSyncPolicy.BEST_EFFORT
    # sync_tolerance_ms: float = 50.0

    # # Optional build-time channel hint.  Strict validation belongs in
    # # ValidateSceneJobParams.require_target_channels.
    # required_channels: list[str] = Field(default_factory=list)
    # missing_channel_policy: MissingChannelPolicy = (
    #     MissingChannelPolicy.KEEP_WITH_WARNING
    # )

    # metadata: JsonDict = Field(default_factory=dict)
