from __future__ import annotations

from pydantic import Field

from sceneops_core.artifacts.schemas import ArtifactRef
from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class ModelArtifactManifest(SceneOpsBaseModel):
    model_id: str
    model_version: str

    artifacts: list[ArtifactRef] = Field(default_factory=list)

    config_uri: str | None = None
    weights_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
