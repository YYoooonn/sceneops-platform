from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class BaseJobParams(SceneOpsBaseModel):
    metadata: JsonDict = Field(default_factory=dict)
