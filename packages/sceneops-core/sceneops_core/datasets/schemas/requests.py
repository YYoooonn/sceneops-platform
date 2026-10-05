from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel


class CreateDatasetRequest(SceneOpsBaseModel):
    dataset_id: str
    name: str | None = None
    description: str | None = None
