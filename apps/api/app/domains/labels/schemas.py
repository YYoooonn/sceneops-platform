from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.labels.schemas.runs import (
    DatasetAutoLabelRunRecord,
    SceneAutoLabelRunRecord,
)


class SceneLabelRunResponse(SceneOpsBaseModel):
    run: SceneAutoLabelRunRecord


class SceneLabelRunListResponse(SceneOpsBaseModel):
    runs: list[SceneAutoLabelRunRecord]
    count: int


class DatasetLabelRunResponse(SceneOpsBaseModel):
    run: DatasetAutoLabelRunRecord


class DatasetLabelRunListResponse(SceneOpsBaseModel):
    runs: list[DatasetAutoLabelRunRecord]
    count: int
