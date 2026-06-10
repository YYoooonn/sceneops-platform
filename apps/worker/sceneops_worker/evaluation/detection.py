from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.evaluations.contracts import Evaluator
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore

from . import utils


DEFAULT_MATCH_DISTANCE_M = 2.0


@dataclass(frozen=True)
class DetectionEvaluationRequest:
    dataset_manifest: DatasetManifest
    scene_artifact_store: SceneArtifactStore
    run_artifact_store: RunArtifactStore
    inference_run_id: str
    evaluation_run_id: str
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M


DetectionEvaluationResult: TypeAlias = DetectionEvaluationManifest


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
    scene_artifact_store: SceneArtifactStore,
    run_artifact_store: RunArtifactStore,
    inference_run_id: str,
    evaluation_run_id: str,
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M,
) -> DetectionEvaluationManifest:
    """Compatibility wrapper for older call sites.

    New code should prefer CenterDistanceDetectionEvaluator.run().
    """

    evaluator = CenterDistanceDetectionEvaluator()
    return await evaluator.run(
        DetectionEvaluationRequest(
            dataset_manifest=dataset_manifest,
            scene_artifact_store=scene_artifact_store,
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
    scene_artifact_store = request.scene_artifact_store
    run_artifact_store = request.run_artifact_store
    inference_run_id = request.inference_run_id
    evaluation_run_id = request.evaluation_run_id
    match_distance_m = request.match_distance_m

    inference_manifest = await run_artifact_store.load_inference_prediction_manifest(
        run_id=inference_run_id
    )
    prediction_shards = inference_manifest.prediction_shards

    # Build sample index from scene manifests (samples are embedded, not individual files)
    sample_index: dict[str, SceneSampleManifest] = {}
    for scene_entry in dataset_manifest.scenes:
        scene_manifest = await scene_artifact_store.load_scene_manifest(
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
    total_raw_prediction_count = 0
    total_lifting_failed_count = 0
    class_stats: dict[str, dict[str, float]] = {}

    for shard in prediction_shards:
        sample_payload = await run_artifact_store.load_sample_prediction_manifest(
            uri=shard.uri
        )
        sample_id = sample_payload["sample_id"]

        sample_manifest = sample_index.get(sample_id)

        if sample_manifest is None:
            raise FileNotFoundError(
                f"Sample manifest not found for sample_id: {sample_id}"
            )

        sample_eval = utils.evaluate_sample(
            sample=sample_manifest,
            predictions=sample_payload.get("predictions", []),
            match_distance_m=match_distance_m,
            dataset_id=sample_payload.get("dataset_id"),
            dataset_version=sample_payload.get("dataset_version"),
        )

        total_tp += sample_eval["tp"]
        total_fp += sample_eval["fp"]
        total_fn += sample_eval["fn"]
        total_distance_error += sample_eval["total_center_distance_error"]
        matched_count += sample_eval["matched_count"]
        total_raw_prediction_count += sample_eval.get(
            "prediction_count", sample_eval["tp"] + sample_eval["fp"]
        )
        total_lifting_failed_count += sample_eval.get(
            "lifting_failed_prediction_count", 0
        )

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
    metrics_uri = run_artifact_store.evaluation_run_metrics_uri(evaluation_run_id)
    samples_root_uri = run_artifact_store.evaluation_samples_root_uri(evaluation_run_id)

    evaluable_prediction_count = total_tp + total_fp
    prediction_count = total_raw_prediction_count
    ground_truth_count = total_tp + total_fn

    metrics = {
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "mean_center_distance_error": round(mean_center_distance_error, 6),
        "evaluable_prediction_count": evaluable_prediction_count,
        "lifting_failed_prediction_count": total_lifting_failed_count,
    }

    primary_metric_value = metrics.get("precision")
    primary_metric_name = "precision" if primary_metric_value is not None else None

    evaluation_manifest = DetectionEvaluationManifest(
        evaluation_run_id=evaluation_run_id,
        inference_run_id=inference_run_id,
        dataset_id=dataset_manifest.dataset_id,
        dataset_version=dataset_manifest.dataset_version,
        model_id=inference_manifest.model_id,
        model_version=inference_manifest.model_version,
        status="succeeded",
        match_distance_m=match_distance_m,
        sample_count=len(prediction_shards),
        prediction_count=prediction_count,
        evaluable_prediction_count=evaluable_prediction_count,
        lifting_failed_prediction_count=total_lifting_failed_count,
        ground_truth_count=ground_truth_count,
        evaluation_unit="annotation",
        primary_metric_name=primary_metric_name,
        primary_metric_value=primary_metric_value,
        evaluation_manifest_uri=evaluation_manifest_uri,
        metrics_uri=metrics_uri,
        samples_root_uri=samples_root_uri,
        metrics=metrics,
        class_metrics=utils.finalize_class_metrics(class_stats),
        created_at=utc_now(),
    )

    await run_artifact_store.write_evaluation_run_manifest(
        evaluation_run_id=evaluation_run_id,
        manifest=evaluation_manifest.model_dump(mode="json"),
    )

    return evaluation_manifest
