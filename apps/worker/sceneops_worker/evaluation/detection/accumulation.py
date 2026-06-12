"""EvaluationAccumulator — aggregates per-sample metrics into run-level totals.

Algorithm-agnostic: center-distance and future evaluators share this accumulator.
The center-distance-specific fields (total_distance_error, matched_count) are
accumulated here and included in build_metrics() for compatibility. Future
evaluators that don't use distance-based matching can simply ignore those fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_worker.evaluation.detection import utils


@dataclass
class EvaluationAccumulator:
    total_tp: int = 0
    total_fp: int = 0
    total_fn: int = 0
    total_distance_error: float = 0.0
    matched_count: int = 0
    raw_prediction_count: int = 0
    lifting_failed_prediction_count: int = 0
    class_stats: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, sample_eval: dict[str, Any]) -> None:
        """Accumulate one sample's evaluation result."""
        self.total_tp += sample_eval["tp"]
        self.total_fp += sample_eval["fp"]
        self.total_fn += sample_eval["fn"]
        self.total_distance_error += sample_eval["total_center_distance_error"]
        self.matched_count += sample_eval["matched_count"]
        self.raw_prediction_count += sample_eval.get(
            "prediction_count",
            sample_eval["tp"] + sample_eval["fp"],
        )
        self.lifting_failed_prediction_count += sample_eval.get(
            "lifting_failed_prediction_count",
            0,
        )
        utils.merge_class_stats(self.class_stats, sample_eval["class_metrics"])

    @property
    def evaluable_prediction_count(self) -> int:
        return self.total_tp + self.total_fp

    @property
    def ground_truth_count(self) -> int:
        return self.total_tp + self.total_fn

    def build_metrics(self) -> dict[str, Any]:
        precision = utils.safe_div(self.total_tp, self.total_tp + self.total_fp)
        recall = utils.safe_div(self.total_tp, self.total_tp + self.total_fn)
        mean_center_distance_error = utils.safe_div(
            self.total_distance_error,
            self.matched_count,
        )
        return {
            "tp": self.total_tp,
            "fp": self.total_fp,
            "fn": self.total_fn,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "mean_center_distance_error": round(mean_center_distance_error, 6),
            "evaluable_prediction_count": self.evaluable_prediction_count,
            "lifting_failed_prediction_count": self.lifting_failed_prediction_count,
        }

    def build_class_metrics(self) -> dict[str, Any]:
        return utils.finalize_class_metrics(self.class_stats)
