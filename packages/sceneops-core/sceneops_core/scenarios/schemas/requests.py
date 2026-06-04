from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .config import ScenarioCurationConfig


class MineScenariosRequest(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    scene_ids: list[str] | None = None

    config: ScenarioCurationConfig = Field(default_factory=ScenarioCurationConfig)

    output_scenario_set_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class GetScenarioRequest(SceneOpsBaseModel):
    scenario_id: str


class GetScenarioSetRequest(SceneOpsBaseModel):
    scenario_set_id: str
