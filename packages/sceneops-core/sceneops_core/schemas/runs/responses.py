from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.runs.records import (
    EvaluationRunRecord,
    InferenceRunRecord,
)


class InferenceRunListResponse(SceneOpsBaseModel):
    runs: list[InferenceRunRecord]
    count: int


class InferenceRunDetailResponse(SceneOpsBaseModel):
    run: InferenceRunRecord


class EvaluationRunListResponse(SceneOpsBaseModel):
    runs: list[EvaluationRunRecord]
    count: int


class EvaluationRunDetailResponse(SceneOpsBaseModel):
    run: EvaluationRunRecord


class RunArtifactResponse(SceneOpsBaseModel):
    artifact: JsonDict


class RunArtifactListResponse(SceneOpsBaseModel):
    artifacts: list[JsonDict]
    count: int
