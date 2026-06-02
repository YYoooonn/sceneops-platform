from __future__ import annotations

from sceneops_core.evaluations.schemas import (
    EvaluationMetricKey,
    EvaluationMetricSpec,
    EvaluationTaskType,
    LeaderboardSortBy,
    MetricDirection,
)


DETECTION_METRIC_SPECS: list[EvaluationMetricSpec] = [
    EvaluationMetricSpec(
        key=EvaluationMetricKey.PRECISION.value,
        label="Precision",
        direction=MetricDirection.HIGHER_IS_BETTER,
    ),
    EvaluationMetricSpec(
        key=EvaluationMetricKey.RECALL.value,
        label="Recall",
        direction=MetricDirection.HIGHER_IS_BETTER,
    ),
    EvaluationMetricSpec(
        key=EvaluationMetricKey.MEAN_CENTER_DISTANCE_ERROR.value,
        label="Mean Center Distance Error",
        direction=MetricDirection.LOWER_IS_BETTER,
        unit="m",
    ),
    EvaluationMetricSpec(
        key=EvaluationMetricKey.SAMPLE_COUNT.value,
        label="Sample Count",
        direction=MetricDirection.HIGHER_IS_BETTER,
    ),
]


_METRIC_SPECS_BY_TASK: dict[EvaluationTaskType, list[EvaluationMetricSpec]] = {
    EvaluationTaskType.DETECTION: DETECTION_METRIC_SPECS,
}


def get_metric_specs_for_task(
    task_type: EvaluationTaskType,
) -> list[EvaluationMetricSpec]:
    return _METRIC_SPECS_BY_TASK.get(task_type, [])


def get_metric_direction(
    *,
    task_type: EvaluationTaskType,
    metric_key: str,
) -> MetricDirection:
    for spec in get_metric_specs_for_task(task_type):
        if spec.key == metric_key:
            return spec.direction

    return MetricDirection.HIGHER_IS_BETTER


def is_descending_sort(
    *,
    task_type: EvaluationTaskType,
    sort_by: LeaderboardSortBy,
) -> bool:
    direction = get_metric_direction(
        task_type=task_type,
        metric_key=sort_by.value,
    )

    return direction == MetricDirection.HIGHER_IS_BETTER
