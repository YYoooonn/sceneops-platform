from __future__ import annotations

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.inference.schemas.runs import InferenceRunRecord


class InferenceRunResponse(SceneOpsBaseModel):
    run: InferenceRunRecord


class InferenceRunListResponse(SceneOpsBaseModel):
    runs: list[InferenceRunRecord]
    count: int


class InferenceMetricsResponse(SceneOpsBaseModel):
    inference_run_id: str
    metrics: JsonDict
