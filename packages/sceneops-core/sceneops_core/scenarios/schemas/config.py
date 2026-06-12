from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import ScenarioSelectionStrategy
from .predicates import PredicateConfig, ScenarioPredicate


class ScenarioSelectionConfig(SceneOpsBaseModel):
    strategy: ScenarioSelectionStrategy = ScenarioSelectionStrategy.TOP_K

    top_k: int | None = 100
    score_threshold: float | None = None

    balance_keys: list[str] = Field(default_factory=list)
    diversity_keys: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class ScenarioCurationConfig(SceneOpsBaseModel):
    predicates: list[ScenarioPredicate] = Field(default_factory=list)
    typed_predicates: list[PredicateConfig] = Field(default_factory=list)

    selection: ScenarioSelectionConfig = Field(default_factory=ScenarioSelectionConfig)

    include_rejected: bool = False

    metadata: JsonDict = Field(default_factory=dict)
