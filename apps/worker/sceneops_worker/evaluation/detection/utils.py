from __future__ import annotations

import math
from typing import Any

from sceneops_core.labels import Box3DLabel


class FrameMismatchError(ValueError):
    """A prediction and a label are expressed in different frames. No
    transform is applied between frames, so the comparison would be wrong."""


def is_evaluable_prediction(pred: dict[str, Any]) -> bool:
    """A prediction is scored only if it is a localized 3-D box: it names the
    frame it is expressed in and its lift did not fail. A 2-D detection that
    could not be lifted carries a placeholder box and no frame."""
    return pred.get("lifting_status") != "failed" and pred.get("frame_id") is not None


def evaluate_sample(
    *,
    scene_id: str,
    sample_id: str,
    labels: list[Box3DLabel],
    predictions: list[dict[str, Any]],
    match_distance_m: float,
) -> dict[str, Any]:
    gt_labels = labels

    evaluable_predictions = [p for p in predictions if is_evaluable_prediction(p)]
    lifting_failed_count = sum(
        1 for p in predictions if p.get("lifting_status") == "failed"
    )
    not_localized_count = (
        len(predictions) - len(evaluable_predictions) - lifting_failed_count
    )

    for prediction in evaluable_predictions:
        for label in gt_labels:
            if prediction["frame_id"] != label.box.frame_id:
                raise FrameMismatchError(
                    f"prediction {prediction['prediction_id']!r} is in frame "
                    f"{prediction['frame_id']!r} but label {label.label_id!r} is in "
                    f"frame {label.box.frame_id!r}; no frame transform is applied"
                )

    matched_gt_indices: set[int] = set()
    matched_prediction_indices: set[int] = set()
    matches: list[dict[str, Any]] = []

    for pred_index, prediction in enumerate(evaluable_predictions):
        best_gt_index = None
        best_distance = float("inf")

        for gt_index, gt in enumerate(gt_labels):
            if gt_index in matched_gt_indices:
                continue

            if gt.category != prediction["category_name"]:
                continue

            distance = center_distance(list(gt.box.center_m), prediction["translation"])

            if distance < best_distance:
                best_distance = distance
                best_gt_index = gt_index

        if best_gt_index is not None and best_distance <= match_distance_m:
            matched_gt_indices.add(best_gt_index)
            matched_prediction_indices.add(pred_index)

            gt = gt_labels[best_gt_index]
            matches.append(
                {
                    "label_id": gt.label_id,
                    "prediction_id": prediction["prediction_id"],
                    "category_name": prediction["category_name"],
                    "center_distance": round(best_distance, 6),
                }
            )

    tp = len(matches)
    fp = len(evaluable_predictions) - len(matched_prediction_indices)
    fn = len(gt_labels) - len(matched_gt_indices)
    total_center_distance_error = sum(match["center_distance"] for match in matches)

    class_metrics = build_sample_class_metrics(
        gt_labels=gt_labels,
        predictions=evaluable_predictions,
        matches=matches,
        matched_gt_indices=matched_gt_indices,
        matched_prediction_indices=matched_prediction_indices,
    )

    return {
        "scene_id": scene_id,
        "sample_id": sample_id,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "matched_count": tp,
        "total_center_distance_error": round(total_center_distance_error, 6),
        "mean_center_distance_error": round(
            safe_div(total_center_distance_error, tp),
            6,
        ),
        "precision": round(safe_div(tp, tp + fp), 6),
        "recall": round(safe_div(tp, tp + fn), 6),
        "matches": matches,
        "class_metrics": class_metrics,
        "prediction_count": len(predictions),
        "evaluable_prediction_count": len(evaluable_predictions),
        "lifting_failed_prediction_count": lifting_failed_count,
        "not_localized_prediction_count": not_localized_count,
    }


def center_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def build_sample_class_metrics(
    *,
    gt_labels: list[Box3DLabel],
    predictions: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    matched_gt_indices: set[int],
    matched_prediction_indices: set[int],
) -> dict[str, dict[str, int]]:
    categories = {gt.category for gt in gt_labels} | {
        pred["category_name"] for pred in predictions
    }

    class_metrics = {category: {"tp": 0, "fp": 0, "fn": 0} for category in categories}

    for match in matches:
        class_metrics[match["category_name"]]["tp"] += 1

    for index, prediction in enumerate(predictions):
        if index not in matched_prediction_indices:
            class_metrics[prediction["category_name"]]["fp"] += 1

    for index, gt in enumerate(gt_labels):
        if index not in matched_gt_indices:
            class_metrics[gt.category]["fn"] += 1

    return class_metrics


def merge_class_stats(
    total: dict[str, dict[str, float]],
    sample_class_metrics: dict[str, dict[str, int]],
) -> None:
    for category, metrics in sample_class_metrics.items():
        if category not in total:
            total[category] = {"tp": 0, "fp": 0, "fn": 0}

        total[category]["tp"] += metrics["tp"]
        total[category]["fp"] += metrics["fp"]
        total[category]["fn"] += metrics["fn"]


def finalize_class_metrics(
    class_stats: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    finalized = {}

    for category, metrics in sorted(class_stats.items()):
        tp = metrics["tp"]
        fp = metrics["fp"]
        fn = metrics["fn"]

        finalized[category] = {
            "tp": int(tp),
            "fp": int(fp),
            "fn": int(fn),
            "precision": round(safe_div(tp, tp + fp), 6),
            "recall": round(safe_div(tp, tp + fn), 6),
        }

    return finalized


def safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0

    return numerator / denominator
