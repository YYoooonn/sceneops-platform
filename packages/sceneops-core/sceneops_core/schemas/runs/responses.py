from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.runs.dataset_validation import DatasetValidationRunRecord
from sceneops_core.schemas.runs.dataset_profile import DatasetProfileRunRecord
from sceneops_core.schemas.runs.evaluation import EvaluationRunRecord
from sceneops_core.schemas.runs.inference import InferenceRunRecord


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


class DatasetValidationRunListResponse(SceneOpsBaseModel):
    runs: list[DatasetValidationRunRecord]
    count: int


class DatasetValidationRunDetailResponse(SceneOpsBaseModel):
    run: DatasetValidationRunRecord


class DatasetProfileRunListResponse(SceneOpsBaseModel):
    runs: list[DatasetProfileRunRecord]
    count: int


class DatasetProfileRunDetailResponse(SceneOpsBaseModel):
    run: DatasetProfileRunRecord


class RunArtifactResponse(SceneOpsBaseModel):
    artifact: JsonDict


class RunArtifactListResponse(SceneOpsBaseModel):
    artifacts: list[JsonDict]
    count: int
