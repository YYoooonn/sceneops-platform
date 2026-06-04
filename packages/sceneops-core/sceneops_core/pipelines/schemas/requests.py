from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import PipelineType


class CreatePipelineRunRequest(SceneOpsBaseModel):
    type: PipelineType = PipelineType.DATASET_SCENE_INGESTION

    dataset_id: str | None = None
    dataset_version: str | None = None

    model_id: str | None = None
    model_version: str | None = None

    params: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)
