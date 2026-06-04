from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import ScenarioSourceType, ScenarioStatus
from .predicates import ScenarioPredicate


class ScenarioRecord(SceneOpsBaseModel):
    scenario_id: str

    source_type: ScenarioSourceType = ScenarioSourceType.SCENE

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_id: str
    sample_ids: list[str] = Field(default_factory=list)

    candidate_id: str | None = None

    status: ScenarioStatus = ScenarioStatus.SELECTED

    tags: list[str] = Field(default_factory=list)
    predicates: list[ScenarioPredicate] = Field(default_factory=list)

    score: float | None = None

    scenario_manifest_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
