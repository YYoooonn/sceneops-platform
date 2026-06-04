from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import EvaluationTaskType
from .summaries import EvaluationRunSummaryItem


class ModelVersionEvaluationHistoryResponse(SceneOpsBaseModel):
    model_id: str
    model_version: str

    task_type: EvaluationTaskType | None = None

    runs: list[EvaluationRunSummaryItem] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
