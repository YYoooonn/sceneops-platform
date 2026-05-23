from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.pipelines.enums import PipelineType


class CreatePipelineRunRequest(BaseModel):
    type: PipelineType = PipelineType.DETECTION_VALIDATION

    datasetId: str | None = None
    datasetVersion: str | None = None

    modelId: str | None = None
    modelVersion: str | None = None

    params: JsonDict = Field(default_factory=dict)
