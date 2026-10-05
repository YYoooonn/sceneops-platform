from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord


class ScenarioSetResponse(SceneOpsBaseModel):
    scenario_set: ScenarioSetRecord


class ScenarioSetListResponse(SceneOpsBaseModel):
    scenario_sets: list[ScenarioSetRecord]
    count: int
