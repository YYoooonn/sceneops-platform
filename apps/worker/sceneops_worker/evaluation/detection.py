from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeAlias

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_core.evaluations.contracts import Evaluator
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.runs import RunArtifactStore

from . import utils


DEFAULT_MATCH_DISTANCE_M = 2.0


@dataclass(frozen=True)
class DetectionEvaluationRequest:
    dataset_manifest: DatasetManifest
    dataset_artifact_store: DatasetArtifactStore
    run_artifact_store: RunArtifactStore
    inference_run_id: str
    evaluation_run_id: str
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M


DetectionEvaluationResult: TypeAlias = dict[str, Any]


DetectionEvaluator: TypeAlias = Evaluator[
    DetectionEvaluationRequest,
    DetectionEvaluationResult,
]


class CenterDistanceDetectionEvaluator(DetectionEvaluator):
    @property
    def evaluator_id(self) -> str:
        return "center-distance"

    async def run(
        self,
        request: DetectionEvaluationRequest,
    ) -> DetectionEvaluationResult:
        return await _evaluate_center_distance_detection(request)


async def evaluate_detection_run(
    *,
    dataset_manifest: DatasetManifest,
    dataset_artifact_store: DatasetArtifactStore,
    run_artifact_store: RunArtifactStore,
    inference_run_id: str,
    evaluation_run_id: str,
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M,
) -> dict[str, Any]:
    """Compatibility wrapper for older call sites.

    New code should prefer CenterDistanceDetectionEvaluator.run().
    """

    evaluator = CenterDistanceDetectionEvaluator()
    return await evaluator.run(
        DetectionEvaluationRequest(
            dataset_manifest=dataset_manifest,
            dataset_artifact_store=dataset_artifact_store,
            run_artifact_store=run_artifact_store,
            inference_run_id=inference_run_id,
            evaluation_run_id=evaluation_run_id,
            match_distance_m=match_distance_m,
        )
    )


async def _evaluate_center_distance_detection(
    request: DetectionEvaluationRequest,
) -> DetectionEvaluationResult:
    dataset_manifest = request.dataset_manifest
    dataset_artifact_store = request.dataset_artifact_store
    run_artifact_store = request.run_artifact_store
    inference_run_id = request.inference_run_id
    evaluation_run_id = request.evaluation_run_id
    match_distance_m = request.match_distance_m

    inference_run = await run_artifact_store.load_inference_run_manifest(
        run_id=inference_run_id
    )
    prediction_uris = await run_artifact_store.list_prediction_manifest_uris(
        run_id=inference_run_id
    )

    # Build sample index from scene manifests (samples are embedded, not individual files)
    sample_index: dict[str, SceneSampleManifest] = {}
    for scene_entry in dataset_manifest.scenes:
        scene_manifest = await dataset_artifact_store.load_scene_manifest(
            scene_entry.scene_manifest_uri
        )
        if scene_manifest is None:
            continue
        for sample in scene_manifest.samples:
            sample_index[sample.sample_id] = sample

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
        sample_id = prediction_manifest["sample_id"]

        sample_manifest = sample_index.get(sample_id)

        if sample_manifest is None:
            raise FileNotFoundError(
                f"Sample manifest not found for sample_id: {sample_id}"
            )

        sample_eval = utils.evaluate_sample(
            sample=sample_manifest,
            predictions=prediction_manifest.get("predictions", []),
            match_distance_m=match_distance_m,
            dataset_id=prediction_manifest.get("dataset_id"),
            dataset_version=prediction_manifest.get("dataset_version"),
        )

        total_tp += sample_eval["tp"]
        total_fp += sample_eval["fp"]
        total_fn += sample_eval["fn"]
        total_distance_error += sample_eval["total_center_distance_error"]
        matched_count += sample_eval["matched_count"]

        utils.merge_class_stats(class_stats, sample_eval["class_metrics"])

        await run_artifact_store.write_sample_evaluation_manifest(
            evaluation_run_id=evaluation_run_id,
            sample_id=sample_id,
            manifest=sample_eval,
        )

    precision = utils.safe_div(total_tp, total_tp + total_fp)
    recall = utils.safe_div(total_tp, total_tp + total_fn)
    mean_center_distance_error = utils.safe_div(total_distance_error, matched_count)

    evaluation_manifest_uri = run_artifact_store.evaluation_run_manifest_uri(
        evaluation_run_id
    )
    samples_root_uri = run_artifact_store.evaluation_samples_root_uri(evaluation_run_id)

    evaluation_manifest = {
        "evaluation_run_id": evaluation_run_id,
        "inference_run_id": inference_run_id,
        "dataset_id": dataset_manifest.dataset_id,
        "dataset_version": dataset_manifest.dataset_version,
        "model_id": inference_run["model_id"],
        "model_version": inference_run["model_version"],
        "status": "succeeded",
        "match_distance_m": match_distance_m,
        "sample_count": len(prediction_uris),
        "evaluation_manifest_uri": evaluation_manifest_uri,
        "samples_root_uri": samples_root_uri,
        "metrics": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "mean_center_distance_error": round(mean_center_distance_error, 6),
        },
        "class_metrics": utils.finalize_class_metrics(class_stats),
        "created_at": datetime.now(UTC).isoformat(),
    }

    await run_artifact_store.write_evaluation_run_manifest(
        evaluation_run_id=evaluation_run_id,
        manifest=evaluation_manifest,
    )

    return evaluation_manifest
