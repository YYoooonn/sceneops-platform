from typing import Literal

from pydantic import BaseModel


EvaluationStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]


class DetectionMetrics(BaseModel):
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    meanCenterDistanceError: float


class DetectionClassMetrics(BaseModel):
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float


class DetectionMatch(BaseModel):
    annotationToken: str
    predictionId: str
    categoryName: str
    centerDistance: float


class DetectionSampleEvaluation(BaseModel):
    datasetId: str
    datasetVersion: str
    sceneId: str
    sampleId: str
    tp: int
    fp: int
    fn: int
    matchedCount: int
    totalCenterDistanceError: float
    meanCenterDistanceError: float
    precision: float
    recall: float
    matches: list[DetectionMatch]
    classMetrics: dict[str, dict[str, int]]


class DetectionEvaluationRunManifest(BaseModel):
    evaluationRunId: str
    inferenceRunId: str
    datasetId: str
    datasetVersion: str
    modelId: str
    modelVersion: str
    status: EvaluationStatus
    matchDistanceM: float
    sampleCount: int
    metrics: DetectionMetrics
    classMetrics: dict[str, DetectionClassMetrics]
    createdAt: str


class EvaluationRunListResponse(BaseModel):
    evaluations: list[DetectionEvaluationRunManifest]
    count: int


class SampleEvaluationListResponse(BaseModel):
    samples: list[DetectionSampleEvaluation]
    count: int
