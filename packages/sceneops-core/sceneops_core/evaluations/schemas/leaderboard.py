from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import EvaluationTaskType, LeaderboardSortBy
from .metrics import EvaluationMetricSpec
from .summaries import EvaluationRunSummaryItem


class LeaderboardItem(EvaluationRunSummaryItem):
    rank: int
    sort_value: float | int | str | None = None


class EvaluationLeaderboardResponse(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    task_type: EvaluationTaskType
    sort_by: LeaderboardSortBy

    metric_specs: list[EvaluationMetricSpec] = Field(default_factory=list)
    items: list[LeaderboardItem] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
