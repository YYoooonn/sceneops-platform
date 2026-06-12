from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .config import SceneSegmentationConfig


class BuildScenesRequest(SceneOpsBaseModel):
    raw_log_id: str

    dataset_id: str
    dataset_version: str

    segmentation: SceneSegmentationConfig = Field(
        default_factory=SceneSegmentationConfig
    )

    output_dataset_id: str | None = None
    output_dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class GetSceneRequest(SceneOpsBaseModel):
    scene_id: str
