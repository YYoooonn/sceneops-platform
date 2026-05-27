from __future__ import annotations

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import DatasetType
from sceneops_core.schemas.jobs.enums import JobType


class IngestDatasetJobResult(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str
    dataset_type: DatasetType

    dataset_manifest_uri: str
    scene_count: int | None = None
    sample_count: int

    result_summary: JsonDict = Field(default_factory=dict)


class PredictDetectionJobResult(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    inference_run_id: str
    prediction_manifest_uri: str

    sample_count: int
    result_summary: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobResult(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    inference_run_id: str
    evaluation_run_id: str
    evaluation_manifest_uri: str

    metrics: JsonDict = Field(default_factory=dict)
    sample_count: int | None = None
    result_summary: JsonDict = Field(default_factory=dict)


JobResult = (
    IngestDatasetJobResult
    | PredictDetectionJobResult
    | EvaluateDetectionJobResult
)


def parse_job_result(job_type: JobType, result: JsonDict) -> JobResult:
    if job_type == JobType.INGEST_DATASET:
        return IngestDatasetJobResult.model_validate(result)

    if job_type == JobType.PREDICT_DETECTION:
        return PredictDetectionJobResult.model_validate(result)

    if job_type == JobType.EVALUATE_DETECTION:
        return EvaluateDetectionJobResult.model_validate(result)

    raise ValueError(f"Unsupported job type: {job_type}")
