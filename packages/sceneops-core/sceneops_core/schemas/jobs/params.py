from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import DatasetType
from sceneops_core.schemas.jobs.enums import JobType


class IngestMode(str, Enum):
    UPSERT = "upsert"
    OVERWRITE = "overwrite"


class InferenceBackend(str, Enum):
    MOCK = "mock"
    # ONNX_RUNTIME = "onnx_runtime"
    # TRITON = "triton"


class IngestDatasetJobParams(BaseModel):
    datasetId: str
    datasetVersion: str
    datasetType: DatasetType = DatasetType.NUSCENES

    rawDataRoot: str | None = None
    maxScenes: int | None = None
    mode: IngestMode = IngestMode.UPSERT

    extra: JsonDict = Field(default_factory=dict)


class PredictDetectionJobParams(BaseModel):
    datasetId: str
    datasetVersion: str

    modelId: str
    modelVersion: str
    inferenceBackend: InferenceBackend = InferenceBackend.MOCK

    inferenceRunId: str | None = None
    maxSamples: int | None = None

    modelUri: str | None = None
    endpointUrl: str | None = None
    extra: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobParams(BaseModel):
    datasetId: str
    datasetVersion: str

    inferenceRunId: str
    evaluationRunId: str | None = None

    evaluatorId: str = "center-distance"
    matchDistanceM: float = 2.0
    extra: JsonDict = Field(default_factory=dict)


JobParams = (
    IngestDatasetJobParams
    | PredictDetectionJobParams
    | EvaluateDetectionJobParams
)


def parse_job_params(job_type: JobType, params: JsonDict) -> JobParams:
    if job_type == JobType.INGEST_DATASET:
        return IngestDatasetJobParams.model_validate(params)

    if job_type == JobType.PREDICT_DETECTION:
        return PredictDetectionJobParams.model_validate(params)

    if job_type == JobType.EVALUATE_DETECTION:
        return EvaluateDetectionJobParams.model_validate(params)

    raise ValueError(f"Unsupported job type: {job_type}")
