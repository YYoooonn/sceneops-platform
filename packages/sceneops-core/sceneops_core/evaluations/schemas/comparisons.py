from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import EvaluationTaskType
from .metrics import EvaluationMetricSpec
from .summaries import EvaluationRunSummaryItem


class EvaluationComparisonResponse(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    task_type: EvaluationTaskType

    metric_specs: list[EvaluationMetricSpec] = Field(default_factory=list)
    runs: list[EvaluationRunSummaryItem] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
