from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobParams


class MineScenariosJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    # Candidate profile selects default filter/sort behaviour.
    # User params below refine or override profile defaults.
    candidate_profile: str = "detection_ready"

    has_ground_truth: bool | None = None
    selectable_for_detection: bool | None = None
    min_annotation_count: int | None = None
    max_annotation_count: int | None = None
    required_channels: list[str] = Field(default_factory=list)
    readiness: list[str] | None = None
    exclusion_reason: str | None = None

    sort_by: str = "annotation_count"
    order: str = "desc"
    max_candidates: int = 50

    output_scenario_set_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ScoreScenarioReadinessJobParams(BaseJobParams):
    scenario_set_id: str | None = None
    scenario_set_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    score_profile: str = "evaluation_readiness"
    required_channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
