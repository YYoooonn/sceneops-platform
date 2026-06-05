from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.runs.schemas.enums import RunStatus

from .enums import EvaluationTaskType


class EvaluationRunSummaryItem(SceneOpsBaseModel):
    evaluation_run_id: str
    inference_run_id: str | None = None

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None

    evaluator_id: str | None = None
    task_type: EvaluationTaskType = EvaluationTaskType.DETECTION
    status: RunStatus

    sample_count: int | None = None
    prediction_count: int | None = None
    ground_truth_count: int | None = None
    evaluation_unit: str | None = None

    primary_metric_name: str | None = None
    primary_metric_value: float | None = None

    evaluation_manifest_uri: str | None = None
    metrics_uri: str | None = None

    metrics: dict[str, Any] = Field(default_factory=dict)
    class_metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
