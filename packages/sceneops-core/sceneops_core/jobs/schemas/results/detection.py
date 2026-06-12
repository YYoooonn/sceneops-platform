from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class PredictDetectionJobResult(BaseJobResult):
    inference_run_id: str

    prediction_manifest_uri: str | None = None
    predictions_root_uri: str | None = None

    model_id: str | None = None
    model_version: str | None = None
    inference_backend: str | None = None

    scene_count: int = 0
    sample_count: int = 0
    inference_request_count: int = 0
    prediction_count: int = 0
    evaluable_prediction_count: int = 0
    lifting_succeeded_count: int = 0
    lifting_failed_count: int = 0

    metrics: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobResult(BaseJobResult):
    evaluation_run_id: str

    evaluation_manifest_uri: str | None = None
    metrics_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    model_id: str | None = None
    model_version: str | None = None
    inference_run_id: str | None = None

    sample_count: int | None = None
    prediction_count: int | None = None
    evaluable_prediction_count: int | None = None
    lifting_failed_prediction_count: int | None = None
    ground_truth_count: int | None = None
    evaluation_unit: str | None = None

    primary_metric_name: str | None = None
    primary_metric_value: float | None = None

    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)
    summary: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)
