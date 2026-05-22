from sceneops_core.schemas.runs import *  # noqa: F403

# from typing import Any, Literal

# from pydantic import BaseModel


# RunStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
# RunType = Literal["INFERENCE"]


# class InferenceRunManifest(BaseModel):
#     runId: str
#     runType: RunType
#     datasetId: str
#     datasetVersion: str
#     modelId: str
#     modelVersion: str
#     status: RunStatus
#     sampleCount: int
#     predictionCount: int
#     createdAt: str


# class InferenceRunIndexItem(BaseModel):
#     runId: str
#     runType: RunType
#     datasetId: str
#     datasetVersion: str
#     modelId: str
#     modelVersion: str
#     status: RunStatus
#     sampleCount: int
#     predictionCount: int
#     createdAt: str


# class DetectionPrediction(BaseModel):
#     predictionId: str
#     categoryName: str
#     translation: list[float]
#     size: list[float]
#     rotation: list[float]
#     score: float
#     sourceAnnotationToken: str | None = None


# class PredictionManifest(BaseModel):
#     runId: str
#     datasetId: str
#     datasetVersion: str
#     modelId: str
#     modelVersion: str
#     sceneId: str
#     sampleId: str
#     predictions: list[DetectionPrediction]


# class InferenceRunListResponse(BaseModel):
#     runs: list[InferenceRunIndexItem]
#     count: int


# class PredictionListResponse(BaseModel):
#     predictions: list[PredictionManifest]
#     count: int


# class RunQuery(BaseModel):
#     datasetId: str | None = None
#     datasetVersion: str | None = None
#     modelId: str | None = None
#     modelVersion: str | None = None
#     status: str | None = None


# class RawPredictionManifest(BaseModel):
#     """
#     필요할 때 자유로운 prediction artifact를 허용하기 위한 fallback schema.
#     지금은 DetectionPrediction을 쓰지만, 나중에 segmentation/tracking으로 확장할 수 있음.
#     """

#     runId: str
#     datasetId: str
#     datasetVersion: str
#     modelId: str
#     modelVersion: str
#     sceneId: str
#     sampleId: str
#     predictions: list[dict[str, Any]]
