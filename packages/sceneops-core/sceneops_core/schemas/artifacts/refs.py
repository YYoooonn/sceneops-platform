from __future__ import annotations

from pydantic import Field

from sceneops_core.schemas.artifacts.enums import ArtifactKind
from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict


class ArtifactRef(SceneOpsBaseModel):
    kind: ArtifactKind
    uri: str
    media_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
