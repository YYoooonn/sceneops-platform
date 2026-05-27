from __future__ import annotations

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.pipelines.enums import PipelineType


class CreatePipelineRunRequest(SceneOpsBaseModel):
    type: PipelineType = PipelineType.DETECTION_VALIDATION

    dataset_id: str | None = None
    dataset_version: str | None = None

    model_id: str | None = None
    model_version: str | None = None

    params: JsonDict = Field(default_factory=dict)
