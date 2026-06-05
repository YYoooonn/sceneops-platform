from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.scenes.schemas.records import SceneRecord


class SceneDetailResponse(SceneOpsBaseModel):
    scene: SceneRecord


class SceneListResponse(SceneOpsBaseModel):
    scenes: list[SceneRecord]
    count: int
