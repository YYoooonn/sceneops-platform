from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord


class CreateScenarioSetRequest(SceneOpsBaseModel):
    name: str | None = None
    description: str | None = None
    dataset_id: str | None = None
    dataset_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class ScenarioSetResponse(SceneOpsBaseModel):
    scenario_set: ScenarioSetRecord


class ScenarioSetListResponse(SceneOpsBaseModel):
    scenario_sets: list[ScenarioSetRecord]
    count: int
