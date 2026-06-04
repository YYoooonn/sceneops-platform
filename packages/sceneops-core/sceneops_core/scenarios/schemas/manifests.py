from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .records import ScenarioRecord


class ScenarioSetManifest(SceneOpsBaseModel):
    scenario_set_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    name: str | None = None
    description: str | None = None

    scenarios: list[ScenarioRecord] = Field(default_factory=list)

    scenario_count: int = 0

    tags: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class ScenarioMiningReport(SceneOpsBaseModel):
    scenario_set_id: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    candidate_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0

    predicate_summary: JsonDict = Field(default_factory=dict)
    score_summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)
