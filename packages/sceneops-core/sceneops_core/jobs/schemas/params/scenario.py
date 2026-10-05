from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict
from sceneops_core.sample_views.schemas import SampleViewRef
from sceneops_core.scenarios.schemas.manifests import ScenarioSortKey, SortOrder

from .base import BaseJobParams


class MineScenariosJobParams(BaseJobParams):
    """Curate a ScenarioSet from explicit, pinned SceneSampleView revisions
    (ADR-007 §33.4).

    Label criteria (``require_labels``, ``min_label_count``,
    ``max_label_count``) count the labels of ``label_set_id``, which every
    input view must pin. ``readiness`` lists the Scene readiness values that
    qualify (derived from validation runs of the exact pinned Scene
    revision); None applies no readiness criterion.
    """

    dataset_id: str
    dataset_version: str

    sample_views: list[SampleViewRef] = Field(min_length=1)

    label_set_id: str | None = None
    require_labels: bool = False
    min_label_count: int | None = Field(default=None, ge=0)
    max_label_count: int | None = Field(default=None, ge=0)
    min_sample_count: int | None = Field(default=None, ge=1)
    required_channels: list[str] = Field(default_factory=list)
    readiness: list[str] | None = None

    sort_by: ScenarioSortKey = ScenarioSortKey.LABEL_COUNT
    order: SortOrder = SortOrder.DESC
    max_candidates: int = Field(default=50, ge=1)

    output_scenario_set_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _label_criteria_need_a_label_set(self) -> MineScenariosJobParams:
        uses_labels = (
            self.require_labels
            or self.min_label_count is not None
            or self.max_label_count is not None
        )
        if uses_labels and self.label_set_id is None:
            raise ValueError("label criteria require label_set_id")
        return self


class ScoreScenarioReadinessJobParams(BaseJobParams):
    scenario_set_id: str | None = None

    score_profile: str = "evaluation_readiness"
    required_channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
