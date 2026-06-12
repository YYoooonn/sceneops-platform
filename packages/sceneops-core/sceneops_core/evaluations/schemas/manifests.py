from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class DetectionEvaluationManifest(SceneOpsBaseModel):
    """Typed schema for the evaluation run manifest artifact (evaluation.json)."""

    evaluation_run_id: str
    inference_run_id: str | None = None

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None

    status: str = "succeeded"
    match_distance_m: float | None = None

    sample_count: int | None = None
    prediction_count: int | None = None
    evaluable_prediction_count: int | None = None
    lifting_failed_prediction_count: int | None = None
    ground_truth_count: int | None = None
    evaluation_unit: str | None = None

    primary_metric_name: str | None = None
    primary_metric_value: float | None = None

    evaluation_manifest_uri: str | None = None
    metrics_uri: str | None = None
    samples_root_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
