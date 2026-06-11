from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel


class SceneProfileResult(SceneOpsBaseModel):
    scene_id: str

    sample_count: int = 0
    frame_count: int = 0
    annotation_count: int = 0

    channels: list[str] = Field(default_factory=list)
    category_distribution: dict[str, int] = Field(default_factory=dict)

    calibration_coverage: dict[str, float] = Field(default_factory=dict)
    ego_pose_coverage: dict[str, float] = Field(default_factory=dict)
    camera_intrinsic_coverage: dict[str, float] = Field(default_factory=dict)
    image_size_coverage: dict[str, float] = Field(default_factory=dict)
