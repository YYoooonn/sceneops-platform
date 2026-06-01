from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict

from .enums import ArtifactKind


class ArtifactRef(SceneOpsBaseModel):
    kind: ArtifactKind
    uri: str
    media_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
