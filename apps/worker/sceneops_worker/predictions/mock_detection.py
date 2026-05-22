from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sceneops_core.paths.datasets import dataset_version_root
from sceneops_core.paths.runs import inference_run_root

from sceneops_worker.runs.manifest_store import JsonStore
# from sceneops_worker.runs.paths import dataset_version_root, inference_run_root


SUPPORTED_CATEGORIES = {
    "vehicle.car",
    "human.pedestrian.adult",
    "movable_object.barrier",
}


def generate_mock_predictions(
    *,
    manifest_root: Path,
    runs_root: Path,
    dataset_id: str,
    dataset_version: str,
    model_id: str,
    model_version: str,
    run_id: str,
    max_samples: int | None = None,
    seed: int = 42,
) -> None:
    random.seed(seed)

    store = JsonStore()

    version_root = dataset_version_root(
        manifest_root=manifest_root,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    run_root = inference_run_root(runs_root=runs_root, run_id=run_id)

    scenes = store.read_json(version_root / "scenes.json")
    if scenes is None:
        raise FileNotFoundError(
            f"scenes.json not found: {version_root / 'scenes.json'}"
        )

    sample_ids = _collect_sample_ids(
        store=store,
        version_root=version_root,
        scenes=scenes,
    )

    if max_samples is not None:
        sample_ids = sample_ids[:max_samples]

    prediction_count = 0

    for sample_id in sample_ids:
        sample = store.read_json(version_root / "samples" / f"{sample_id}.json")
        if sample is None:
            continue

        predictions = _build_predictions_from_sample(sample)
        prediction_count += len(predictions)

        prediction_manifest = {
            "runId": run_id,
            "datasetId": dataset_id,
            "datasetVersion": dataset_version,
            "modelId": model_id,
            "modelVersion": model_version,
            "sceneId": sample["sceneId"],
            "sampleId": sample_id,
            "predictions": predictions,
        }

        store.write_json(
            run_root / "predictions" / f"{sample_id}.json",
            prediction_manifest,
        )

    run_manifest = {
        "runId": run_id,
        "runType": "INFERENCE",
        "datasetId": dataset_id,
        "datasetVersion": dataset_version,
        "modelId": model_id,
        "modelVersion": model_version,
        "status": "SUCCEEDED",
        "sampleCount": len(sample_ids),
        "predictionCount": prediction_count,
        "createdAt": datetime.now(UTC).isoformat(),
    }

    store.write_json(run_root / "run.json", run_manifest)


def _collect_sample_ids(
    *,
    store: JsonStore,
    version_root: Path,
    scenes: list[dict[str, Any]],
) -> list[str]:
    sample_ids: list[str] = []

    for scene_index in scenes:
        scene_id = scene_index["sceneId"]
        scene = store.read_json(version_root / "scenes" / f"{scene_id}.json")
        if scene is None:
            continue

        sample_ids.extend(scene.get("sampleIds", []))

    return sample_ids


def _build_predictions_from_sample(sample: dict[str, Any]) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []

    annotations = sample.get("annotations", [])

    for index, annotation in enumerate(annotations):
        category_name = annotation["categoryName"]

        if category_name not in SUPPORTED_CATEGORIES:
            continue

        # 일부 GT는 intentionally miss 처리해서 FN 발생시키기
        if random.random() < 0.15:
            continue

        translation = _perturb_translation(annotation["translation"])
        size = _perturb_size(annotation["size"])

        predictions.append(
            {
                "predictionId": f"{sample['sampleId']}-pred-{index:04d}",
                "categoryName": category_name,
                "translation": translation,
                "size": size,
                "rotation": annotation["rotation"],
                "score": round(random.uniform(0.55, 0.98), 4),
                "sourceAnnotationToken": annotation["annotationToken"],
            }
        )

    # 일부 false positive 추가
    if random.random() < 0.25:
        predictions.append(_build_false_positive(sample["sampleId"]))

    return predictions


def _perturb_translation(translation: list[float]) -> list[float]:
    return [
        round(translation[0] + random.uniform(-0.8, 0.8), 4),
        round(translation[1] + random.uniform(-0.8, 0.8), 4),
        round(translation[2] + random.uniform(-0.2, 0.2), 4),
    ]


def _perturb_size(size: list[float]) -> list[float]:
    return [
        round(max(0.1, size[0] + random.uniform(-0.2, 0.2)), 4),
        round(max(0.1, size[1] + random.uniform(-0.2, 0.2)), 4),
        round(max(0.1, size[2] + random.uniform(-0.2, 0.2)), 4),
    ]


def _build_false_positive(sample_id: str) -> dict[str, Any]:
    return {
        "predictionId": f"{sample_id}-fp-0000",
        "categoryName": "vehicle.car",
        "translation": [
            round(random.uniform(-30.0, 30.0), 4),
            round(random.uniform(-30.0, 30.0), 4),
            round(random.uniform(0.0, 2.0), 4),
        ],
        "size": [4.2, 1.8, 1.6],
        "rotation": [1.0, 0.0, 0.0, 0.0],
        "score": round(random.uniform(0.3, 0.7), 4),
        "sourceAnnotationToken": None,
    }
