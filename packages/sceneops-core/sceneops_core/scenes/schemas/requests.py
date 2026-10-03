from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel


class GetSceneRequest(SceneOpsBaseModel):
    scene_id: str
