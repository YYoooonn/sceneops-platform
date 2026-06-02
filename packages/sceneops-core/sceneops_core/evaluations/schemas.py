from __future__ import annotations

from enum import StrEnum
from typing import Any

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.runs.schemas import RunStatus


class EvaluationTaskType(StrEnum):
    DETECTION = "detection"
    TRACKING = "tracking"
    SEGMENTATION = "segmentation"
    DATASET_VALIDATION = "dataset_validation"
    CUSTOM = "custom"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class EvaluationMetricKey(StrEnum):
    PRECISION = "precision"
    RECALL = "recall"
    MEAN_CENTER_DISTANCE_ERROR = "mean_center_distance_error"
    SAMPLE_COUNT = "sample_count"
    CREATED_AT = "created_at"


class EvaluationMetricSpec(SceneOpsBaseModel):
    key: str
    label: str
    direction: MetricDirection
    unit: str | None = None
    description: str | None = None


class EvaluationMetricValue(SceneOpsBaseModel):
    key: str
    value: float | int | str | None = None
    direction: MetricDirection | None = None
    rankable: bool = True


class EvaluationRunSummaryItem(SceneOpsBaseModel):
    evaluation_run_id: str
    inference_run_id: str | None = None

    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    evaluator_id: str | None = None
    task_type: EvaluationTaskType = EvaluationTaskType.DETECTION
    status: RunStatus

    sample_count: int | None = None
    evaluation_manifest_uri: str | None = None

    metrics: dict[str, Any] = {}
    class_metrics: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class EvaluationComparisonResponse(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str
    task_type: EvaluationTaskType
    metric_specs: list[EvaluationMetricSpec]
    runs: list[EvaluationRunSummaryItem]


class ModelVersionEvaluationHistoryResponse(SceneOpsBaseModel):
    model_id: str
    model_version: str
    task_type: EvaluationTaskType | None = None
    runs: list[EvaluationRunSummaryItem]


class LeaderboardSortBy(StrEnum):
    PRECISION = "precision"
    RECALL = "recall"
    MEAN_CENTER_DISTANCE_ERROR = "mean_center_distance_error"
    SAMPLE_COUNT = "sample_count"
    CREATED_AT = "created_at"


class LeaderboardItem(EvaluationRunSummaryItem):
    rank: int
    sort_value: float | int | str | None = None


class EvaluationLeaderboardResponse(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str
    task_type: EvaluationTaskType
    sort_by: LeaderboardSortBy
    metric_specs: list[EvaluationMetricSpec]
    items: list[LeaderboardItem]
