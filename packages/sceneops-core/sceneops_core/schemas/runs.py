from enum import Enum
from typing import Any

from pydantic import BaseModel


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class RunType(str, Enum):
    INFERENCE = "INFERENCE"


class InferenceRunManifest(BaseModel):
    runId: str
    runType: RunType
    datasetId: str
    datasetVersion: str
    modelId: str
    modelVersion: str
    status: RunStatus
    sampleCount: int
    predictionCount: int
    createdAt: str


class InferenceRunIndexItem(BaseModel):
    runId: str
    runType: RunType
    datasetId: str
    datasetVersion: str
    modelId: str
    modelVersion: str
    status: RunStatus
    sampleCount: int
    predictionCount: int
    createdAt: str


class DetectionPrediction(BaseModel):
    predictionId: str
    categoryName: str
    translation: list[float]
    size: list[float]
    rotation: list[float]
    score: float
    sourceAnnotationToken: str | None = None


class PredictionManifest(BaseModel):
    runId: str
    datasetId: str
    datasetVersion: str
    modelId: str
    modelVersion: str
    sceneId: str
    sampleId: str
    predictions: list[DetectionPrediction]


class InferenceRunListResponse(BaseModel):
    runs: list[InferenceRunIndexItem]
    count: int


class PredictionListResponse(BaseModel):
    predictions: list[PredictionManifest]
    count: int


class RawPredictionManifest(BaseModel):
    runId: str
    datasetId: str
    datasetVersion: str
    modelId: str
    modelVersion: str
    sceneId: str
    sampleId: str
    predictions: list[dict[str, Any]]
