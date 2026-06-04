from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .refs import RunRef


class RunRefListResponse(SceneOpsBaseModel):
    runs: list[RunRef]
    count: int
    metadata: JsonDict = Field(default_factory=dict)


class RunArtifactResponse(SceneOpsBaseModel):
    artifact: JsonDict


class RunArtifactListResponse(SceneOpsBaseModel):
    artifacts: list[JsonDict]
    count: int
