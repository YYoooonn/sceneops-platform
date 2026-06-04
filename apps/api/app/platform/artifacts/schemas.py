from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.common.schemas import SceneOpsBaseModel


class ArtifactResponse(SceneOpsBaseModel):
    artifact: ArtifactRecord


class ArtifactListResponse(SceneOpsBaseModel):
    artifacts: list[ArtifactRecord]
    count: int
