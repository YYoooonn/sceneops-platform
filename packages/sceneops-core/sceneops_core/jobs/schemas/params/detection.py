from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.inference.enums import InferenceBackendType

from .base import BaseJobParams


class PredictDetectionJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    dataset_manifest_uri: str | None = None
    scene_ids: list[str] | None = None

    inference_backend: InferenceBackendType = InferenceBackendType.MOCK

    inference_run_id: str | None = None

    max_samples: int | None = None

    model_uri: str | None = None
    endpoint_url: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    inference_run_id: str

    evaluation_run_id: str | None = None

    evaluator_id: str = "center-distance"
    match_distance_m: float = 2.0

    metadata: JsonDict = Field(default_factory=dict)
