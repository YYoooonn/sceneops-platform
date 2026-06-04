from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import ScenarioSourceType, ScenarioStatus
from .predicates import ScenarioPredicate


class ScenarioCandidate(SceneOpsBaseModel):
    candidate_id: str

    source_type: ScenarioSourceType = ScenarioSourceType.SCENE

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_id: str
    sample_ids: list[str] = Field(default_factory=list)

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None

    predicates: list[ScenarioPredicate] = Field(default_factory=list)

    score: float | None = None
    rank: int | None = None

    status: ScenarioStatus = ScenarioStatus.CANDIDATE

    reason: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
