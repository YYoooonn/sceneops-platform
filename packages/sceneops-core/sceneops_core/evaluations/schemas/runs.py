from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType

from .enums import EvaluationTaskType


class EvaluationRunRecord(BaseRunRecord):
    type: RunType = RunType.EVALUATION

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None

    inference_run_id: str | None = None
    predictions_root_uri: str | None = None

    evaluator_id: str = "center-distance"
    task_type: EvaluationTaskType = EvaluationTaskType.DETECTION

    sample_count: int | None = None
    prediction_count: int | None = None
    ground_truth_count: int | None = None
    evaluation_unit: str | None = None

    primary_metric_name: str | None = None
    primary_metric_value: float | None = None

    evaluation_manifest_uri: str | None = None
    metrics_uri: str | None = None

    summary: JsonDict = Field(default_factory=dict)
    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)
