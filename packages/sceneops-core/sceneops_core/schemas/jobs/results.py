from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import DatasetType
from sceneops_core.schemas.jobs.enums import JobType


class IngestDatasetJobResult(BaseModel):
    datasetId: str
    datasetVersion: str
    datasetType: DatasetType

    manifestUri: str
    sceneCount: int | None = None
    sampleCount: int

    resultSummary: JsonDict = Field(default_factory=dict)


class PredictDetectionJobResult(BaseModel):
    datasetId: str
    datasetVersion: str

    modelId: str
    modelVersion: str

    inferenceRunId: str
    predictionManifestUri: str

    sampleCount: int
    resultSummary: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobResult(BaseModel):
    datasetId: str
    datasetVersion: str

    inferenceRunId: str
    evaluationRunId: str
    evaluationManifestUri: str

    metrics: JsonDict = Field(default_factory=dict)
    sampleCount: int | None = None
    resultSummary: JsonDict = Field(default_factory=dict)


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
