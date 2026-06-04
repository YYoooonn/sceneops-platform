from __future__ import annotations

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord


class EvaluationRunResponse(SceneOpsBaseModel):
    run: EvaluationRunRecord


class EvaluationRunListResponse(SceneOpsBaseModel):
    runs: list[EvaluationRunRecord]
    count: int


class EvaluationMetricsResponse(SceneOpsBaseModel):
    evaluation_run_id: str
    summary: JsonDict
    metrics: JsonDict
    class_metrics: JsonDict
