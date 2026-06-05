from __future__ import annotations

import math
from typing import Any

from sceneops_core.scenes.schemas.manifests import (
    SceneSampleManifest,
    SceneAnnotationManifest as SampleAnnotationManifest,
)


def evaluate_sample(
    *,
    sample: SceneSampleManifest,
    predictions: list[dict[str, Any]],
    match_distance_m: float,
) -> dict[str, Any]:
    gt_annotations = _filter_supported_gt(sample.annotations)

    matched_gt_indices: set[int] = set()
    matched_prediction_indices: set[int] = set()
    matches: list[dict[str, Any]] = []

    for pred_index, prediction in enumerate(predictions):
        best_gt_index = None
        best_distance = float("inf")

        for gt_index, gt in enumerate(gt_annotations):
            if gt_index in matched_gt_indices:
                continue

            if gt.category_name != prediction["category_name"]:
                continue

            distance = center_distance(gt.translation, prediction["translation"])

            if distance < best_distance:
                best_distance = distance
                best_gt_index = gt_index

        if best_gt_index is not None and best_distance <= match_distance_m:
            matched_gt_indices.add(best_gt_index)
            matched_prediction_indices.add(pred_index)

            gt = gt_annotations[best_gt_index]
            matches.append(
                {
                    "annotation_token": gt.annotation_token,
                    "prediction_id": prediction["prediction_id"],
                    "category_name": prediction["category_name"],
                    "center_distance": round(best_distance, 6),
                }
            )

    tp = len(matches)
    fp = len(predictions) - len(matched_prediction_indices)
    fn = len(gt_annotations) - len(matched_gt_indices)
    total_center_distance_error = sum(match["center_distance"] for match in matches)

    class_metrics = build_sample_class_metrics(
        gt_annotations=gt_annotations,
        predictions=predictions,
        matches=matches,
        matched_gt_indices=matched_gt_indices,
        matched_prediction_indices=matched_prediction_indices,
    )

    return {
        "dataset_id": sample.dataset_id,
        "dataset_version": sample.dataset_version,
        "scene_id": sample.scene_id,
        "sample_id": sample.sample_id,
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
    }


def _filter_supported_gt(
    annotations: list[SampleAnnotationManifest],
) -> list[SampleAnnotationManifest]:
    supported_prefixes = (
        "vehicle.car",
        "human.pedestrian",
        "movable_object.barrier",
    )

    return [
        annotation
        for annotation in annotations
        if annotation.category_name.startswith(supported_prefixes)
    ]


def center_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def build_sample_class_metrics(
    *,
    gt_annotations: list[SampleAnnotationManifest],
    predictions: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    matched_gt_indices: set[int],
    matched_prediction_indices: set[int],
) -> dict[str, dict[str, int]]:
    categories = {gt.category_name for gt in gt_annotations} | {
        pred["category_name"] for pred in predictions
    }

    class_metrics = {category: {"tp": 0, "fp": 0, "fn": 0} for category in categories}

    for match in matches:
        class_metrics[match["category_name"]]["tp"] += 1

    for index, prediction in enumerate(predictions):
        if index not in matched_prediction_indices:
            class_metrics[prediction["category_name"]]["fp"] += 1

    for index, gt in enumerate(gt_annotations):
        if index not in matched_gt_indices:
            class_metrics[gt.category_name]["fn"] += 1

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
