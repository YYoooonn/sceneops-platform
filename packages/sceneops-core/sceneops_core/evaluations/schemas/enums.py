from __future__ import annotations

from enum import StrEnum


class EvaluationTaskType(StrEnum):
    DETECTION = "detection"
    TRACKING = "tracking"
    SEGMENTATION = "segmentation"
    AUTO_LABEL_QUALITY = "auto_label_quality"
    DATASET_VALIDATION = "dataset_validation"
    SCENE_RECONSTRUCTION = "scene_reconstruction"
    SCENE_COMPARISON = "scene_comparison"
    SCENARIO_READINESS = "scenario_readiness"
    CUSTOM = "custom"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class EvaluationMetricKey(StrEnum):
    PRECISION = "precision"
    RECALL = "recall"
    F1 = "f1"
    MEAN_CENTER_DISTANCE_ERROR = "mean_center_distance_error"

    GEOMETRY_ERROR = "geometry_error"
    TRAJECTORY_ERROR = "trajectory_error"
    ANNOTATION_F1 = "annotation_f1"
    READINESS_SCORE = "readiness_score"
    BLOCKED_COUNT = "blocked_count"

    SAMPLE_COUNT = "sample_count"
    LABELED_SAMPLE_COUNT = "labeled_sample_count"
    LABELING_COVERAGE = "labeling_coverage"
    CREATED_AT = "created_at"


class LeaderboardSortBy(StrEnum):
    PRECISION = "precision"
    RECALL = "recall"
    F1 = "f1"
    MEAN_CENTER_DISTANCE_ERROR = "mean_center_distance_error"
    SAMPLE_COUNT = "sample_count"
    LABELING_COVERAGE = "labeling_coverage"
    CREATED_AT = "created_at"
