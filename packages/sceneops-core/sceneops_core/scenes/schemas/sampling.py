from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class SampleGroupingStrategy(StrEnum):
    FRAME_ID = "frame_id"
    TIME_BUCKET = "time_bucket"
    NEAREST_TIMESTAMP = "nearest_timestamp"


class SensorSyncPolicy(StrEnum):
    EXACT = "exact"
    WITHIN_TOLERANCE = "within_tolerance"
    BEST_EFFORT = "best_effort"


class MissingChannelPolicy(StrEnum):
    KEEP_WITH_WARNING = "keep_with_warning"
    DROP_SAMPLE = "drop_sample"
    FAIL_SCENE = "fail_scene"


class SampleGroupingConfig(SceneOpsBaseModel):
    """Configuration for grouping raw sensor frames into scene samples."""

    strategy: SampleGroupingStrategy = SampleGroupingStrategy.TIME_BUCKET

    sample_time_window_ms: float | None = 500.0
    reference_channel: str | None = None

    sync_policy: SensorSyncPolicy = SensorSyncPolicy.BEST_EFFORT
    sync_tolerance_ms: float = 50.0

    # Optional build-time channel hint.  Strict validation belongs in
    # ValidateSceneJobParams.require_target_channels.
    required_channels: list[str] = Field(default_factory=list)
    missing_channel_policy: MissingChannelPolicy = (
        MissingChannelPolicy.KEEP_WITH_WARNING
    )

    metadata: JsonDict = Field(default_factory=dict)
