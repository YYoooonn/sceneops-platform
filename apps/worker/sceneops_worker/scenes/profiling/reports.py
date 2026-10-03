from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class SceneProfileResult(SceneOpsBaseModel):
    scene_id: str

    observation_count: int = 0
    keyframe_count: int = 0
    annotation_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)
    observations_by_channel: dict[str, int] = Field(default_factory=dict)
    category_distribution: dict[str, int] = Field(default_factory=dict)

    # Fraction of a channel's observations that carry the property.
    calibration_coverage: dict[str, float] = Field(default_factory=dict)
    ego_pose_coverage: dict[str, float] = Field(default_factory=dict)
    camera_intrinsic_coverage: dict[str, float] = Field(default_factory=dict)
    image_size_coverage: dict[str, float] = Field(default_factory=dict)
