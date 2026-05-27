from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sceneops_core.schemas.datasets import (
    DatasetManifest,
    DatasetSampleManifest,
    SampleAnnotationManifest,
)
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.runs import RunArtifactStore

DEFAULT_MATCH_DISTANCE_M = 2.0


async def evaluate_detection_run(
    *,
    dataset_manifest: DatasetManifest,
    dataset_artifact_store: DatasetArtifactStore,
    run_artifact_store: RunArtifactStore,
    inference_run_id: str,
    evaluation_run_id: str,
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M,
) -> dict[str, Any]:
    inference_run = await run_artifact_store.load_inference_run_manifest(
        run_id=inference_run_id
    )

    prediction_uris = await run_artifact_store.list_prediction_manifest_uris(
        run_id=inference_run_id
    )

    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_distance_error = 0.0
    matched_count = 0
    class_stats: dict[str, dict[str, float]] = {}

    for prediction_uri in prediction_uris:
        prediction_manifest = await run_artifact_store.load_prediction_manifest(
            uri=prediction_uri
        )

        sample_id = prediction_manifest["sampleId"]
        sample_uri = dataset_artifact_store.artifact_store.join_uri(
            dataset_manifest.uris.sample_root,
            f"{sample_id}.json",
        )
        sample_manifest = await dataset_artifact_store.load_sample_manifest(sample_uri)
        if sample_manifest is None:
            continue

        sample_eval = _evaluate_sample(
            sample=sample_manifest,
            predictions=prediction_manifest.get("predictions", []),
            match_distance_m=match_distance_m,
        )

        total_tp += sample_eval["tp"]
        total_fp += sample_eval["fp"]
        total_fn += sample_eval["fn"]
        total_distance_error += sample_eval["totalCenterDistanceError"]
        matched_count += sample_eval["matchedCount"]

        _merge_class_stats(class_stats, sample_eval["classMetrics"])

        await run_artifact_store.write_sample_evaluation_manifest(
            evaluation_run_id=evaluation_run_id,
            sample_id=sample_id,
            manifest=sample_eval,
        )

    precision = _safe_div(total_tp, total_tp + total_fp)
    recall = _safe_div(total_tp, total_tp + total_fn)
    mean_center_distance_error = _safe_div(total_distance_error, matched_count)

    evaluation_manifest_uri = run_artifact_store.evaluation_run_manifest_uri(
        evaluation_run_id
    )
    samples_root_uri = run_artifact_store.evaluation_samples_root_uri(evaluation_run_id)

    evaluation_manifest = {
        "evaluationRunId": evaluation_run_id,
        "inferenceRunId": inference_run_id,
        "datasetId": dataset_manifest.dataset_id,
        "datasetVersion": dataset_manifest.dataset_version,
        "modelId": inference_run["modelId"],
        "modelVersion": inference_run["modelVersion"],
        "status": "succeeded",
        "matchDistanceM": match_distance_m,
        "sampleCount": len(prediction_uris),
        "evaluationManifestUri": evaluation_manifest_uri,
        "samplesRootUri": samples_root_uri,
        "metrics": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "meanCenterDistanceError": round(mean_center_distance_error, 6),
        },
        "classMetrics": _finalize_class_metrics(class_stats),
        "createdAt": datetime.now(UTC).isoformat(),
    }

    await run_artifact_store.write_evaluation_run_manifest(
        evaluation_run_id=evaluation_run_id,
        manifest=evaluation_manifest,
    )

    return evaluation_manifest


def _evaluate_sample(
    *,
    sample: DatasetSampleManifest,
    predictions: list[dict[str, Any]],
    match_distance_m: float,
) -> dict[str, Any]:
    gt_annotations = _filter_supported_gt(sample.annotations)

    matched_gt_indices: set[int] = set()
    matched_prediction_indices: set[int] = set()
    matches = []

    for pred_index, prediction in enumerate(predictions):
        best_gt_index = None
        best_distance = float("inf")

        for gt_index, gt in enumerate(gt_annotations):
            if gt_index in matched_gt_indices:
                continue

            if gt.category_name != prediction["categoryName"]:
                continue

            distance = _center_distance(gt.translation, prediction["translation"])

            if distance < best_distance:
                best_distance = distance
                best_gt_index = gt_index

        if best_gt_index is not None and best_distance <= match_distance_m:
            matched_gt_indices.add(best_gt_index)
            matched_prediction_indices.add(pred_index)

            gt = gt_annotations[best_gt_index]
            matches.append(
                {
                    "annotationToken": gt.annotation_token,
                    "predictionId": prediction["predictionId"],
                    "categoryName": prediction["categoryName"],
                    "centerDistance": round(best_distance, 6),
                }
            )

    tp = len(matches)
    fp = len(predictions) - len(matched_prediction_indices)
    fn = len(gt_annotations) - len(matched_gt_indices)
    total_center_distance_error = sum(match["centerDistance"] for match in matches)

    class_metrics = _build_sample_class_metrics(
        gt_annotations=gt_annotations,
        predictions=predictions,
        matches=matches,
        matched_gt_indices=matched_gt_indices,
        matched_prediction_indices=matched_prediction_indices,
    )

    return {
        "datasetId": sample.dataset_id,
        "datasetVersion": sample.dataset_version,
        "sceneId": sample.scene_id,
        "sampleId": sample.sample_id,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "matchedCount": tp,
        "totalCenterDistanceError": round(total_center_distance_error, 6),
        "meanCenterDistanceError": round(
            _safe_div(total_center_distance_error, tp),
            6,
        ),
        "precision": round(_safe_div(tp, tp + fp), 6),
        "recall": round(_safe_div(tp, tp + fn), 6),
        "matches": matches,
        "classMetrics": class_metrics,
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


def _center_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _build_sample_class_metrics(
    *,
    gt_annotations: list[SampleAnnotationManifest],
    predictions: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    matched_gt_indices: set[int],
    matched_prediction_indices: set[int],
) -> dict[str, dict[str, int]]:
    categories = {gt.category_name for gt in gt_annotations} | {
        pred["categoryName"] for pred in predictions
    }

    class_metrics = {category: {"tp": 0, "fp": 0, "fn": 0} for category in categories}

    for match in matches:
        class_metrics[match["categoryName"]]["tp"] += 1

    for index, prediction in enumerate(predictions):
        if index not in matched_prediction_indices:
            class_metrics[prediction["categoryName"]]["fp"] += 1

    for index, gt in enumerate(gt_annotations):
        if index not in matched_gt_indices:
            class_metrics[gt.category_name]["fn"] += 1

    return class_metrics


def _merge_class_stats(
    total: dict[str, dict[str, float]],
    sample_class_metrics: dict[str, dict[str, int]],
) -> None:
    for category, metrics in sample_class_metrics.items():
        if category not in total:
            total[category] = {"tp": 0, "fp": 0, "fn": 0}

        total[category]["tp"] += metrics["tp"]
        total[category]["fp"] += metrics["fp"]
        total[category]["fn"] += metrics["fn"]


def _finalize_class_metrics(
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
            "precision": round(_safe_div(tp, tp + fp), 6),
            "recall": round(_safe_div(tp, tp + fn), 6),
        }

    return finalized


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
