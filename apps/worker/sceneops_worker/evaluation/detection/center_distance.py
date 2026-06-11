"""Center-distance detection evaluator.

This module is specific to the center-distance matching algorithm.

Evaluation policy:
  - SceneManifest.annotation_count is the scene-level GT availability signal.
  - If the selected dataset has no GT annotations at all, evaluation is skipped.
  - Prediction shards belonging to scenes without GT are skipped.
  - Samples inside GT-bearing scenes are evaluated even when that sample has
    zero annotations, because those are valid negative samples.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_worker.evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_worker.evaluation.detection.artifacts import (
    write_final_evaluation_manifest,
    write_sample_evaluation,
    write_skipped_evaluation_manifest,
)
from sceneops_worker.evaluation.detection.base import (
    DetectionEvaluationRequest,
    DetectionEvaluationResult,
    DetectionEvaluator,
)
from sceneops_worker.evaluation.detection.loading import (
    EvaluationSceneIndex,
    build_scene_index,
    load_prediction_manifest,
    load_sample_prediction_payload,
)

from . import utils


class CenterDistanceDetectionEvaluator(DetectionEvaluator):
    @property
    def evaluator_id(self) -> str:
        return "center-distance"

    async def run(
        self,
        request: DetectionEvaluationRequest,
    ) -> DetectionEvaluationResult:
        return await evaluate_center_distance_detection(request)


async def evaluate_center_distance_detection(
    request: DetectionEvaluationRequest,
) -> DetectionEvaluationResult:
    prediction_manifest = await load_prediction_manifest(request)

    scene_index = await build_scene_index(
        dataset_manifest=request.dataset_manifest,
        scene_artifact_store=request.scene_artifact_store,
    )

    if scene_index.annotation_count == 0:
        return await _handle_missing_dataset_gt(
            request=request,
            prediction_manifest=prediction_manifest,
            scene_index=scene_index,
        )

    accumulator = EvaluationAccumulator()

    evaluated_sample_count = 0
    skipped_shard_count = 0
    skipped_prediction_count = 0
    skipped_shards: list[dict[str, Any]] = []
    evaluated_scene_ids: set[str] = set()
    warnings: list[dict[str, Any]] = []

    for shard in prediction_manifest.prediction_shards:
        scene_entry = scene_index.get_scene(shard.scene_id)

        if scene_entry is not None and not scene_entry.has_ground_truth:
            _raise_if_missing_gt_policy_fail(
                request=request,
                reason=(
                    f"Prediction shard belongs to a scene without ground truth: "
                    f"scene_id={scene_entry.scene_id!r}, sample_id={shard.sample_id!r}"
                ),
            )

            skipped_shard_count += 1
            skipped_prediction_count += int(shard.prediction_count or 0)
            skipped_shards.append(
                {
                    "scene_id": scene_entry.scene_id,
                    "sample_id": shard.sample_id,
                    "uri": shard.uri,
                    "prediction_count": shard.prediction_count,
                    "reason": "scene_has_no_ground_truth",
                }
            )
            continue

        sample_payload = await load_sample_prediction_payload(
            run_artifact_store=request.run_artifact_store,
            uri=shard.uri,
        )

        sample_id = sample_payload["sample_id"]

        if shard.sample_id is not None and shard.sample_id != sample_id:
            warnings.append(
                {
                    "type": "shard_sample_id_mismatch",
                    "shard_sample_id": shard.sample_id,
                    "payload_sample_id": sample_id,
                    "scene_id": shard.scene_id,
                    "uri": shard.uri,
                }
            )

        payload_scene_entry = scene_index.get_scene_for_sample(sample_id)

        if (
            scene_entry is not None
            and payload_scene_entry is not None
            and scene_entry.scene_id != payload_scene_entry.scene_id
        ):
            warnings.append(
                {
                    "type": "shard_scene_id_mismatch",
                    "shard_scene_id": scene_entry.scene_id,
                    "payload_scene_id": payload_scene_entry.scene_id,
                    "sample_id": sample_id,
                    "uri": shard.uri,
                }
            )
            scene_entry = payload_scene_entry

        if scene_entry is None:
            scene_entry = payload_scene_entry

        if scene_entry is None:
            skipped_shard_count += 1
            skipped_prediction_count += int(shard.prediction_count or 0)
            skipped_shards.append(
                {
                    "scene_id": shard.scene_id,
                    "sample_id": sample_id,
                    "uri": shard.uri,
                    "prediction_count": shard.prediction_count,
                    "reason": "sample_not_found_in_scene_index",
                }
            )
            continue

        if not scene_entry.has_ground_truth:
            _raise_if_missing_gt_policy_fail(
                request=request,
                reason=(
                    f"Prediction sample belongs to a scene without ground truth: "
                    f"scene_id={scene_entry.scene_id!r}, sample_id={sample_id!r}"
                ),
            )

            skipped_shard_count += 1
            skipped_prediction_count += int(shard.prediction_count or 0)
            skipped_shards.append(
                {
                    "scene_id": scene_entry.scene_id,
                    "sample_id": sample_id,
                    "uri": shard.uri,
                    "prediction_count": shard.prediction_count,
                    "reason": "scene_has_no_ground_truth",
                }
            )
            continue

        sample_eval = evaluate_center_distance_sample_payload(
            sample_payload=sample_payload,
            sample_index=scene_index.samples_by_id,
            match_distance_m=request.match_distance_m,
        )

        accumulator.add(sample_eval)
        evaluated_sample_count += 1
        evaluated_scene_ids.add(scene_entry.scene_id)

        await write_sample_evaluation(
            run_artifact_store=request.run_artifact_store,
            evaluation_run_id=request.evaluation_run_id,
            sample_id=sample_id,
            sample_eval=sample_eval,
        )

    skipped_scene_ids = sorted(
        {s["scene_id"] for s in skipped_shards if s.get("scene_id")}
    )

    if evaluated_sample_count == 0:
        return await _handle_no_evaluable_shards(
            request=request,
            prediction_manifest=prediction_manifest,
            scene_index=scene_index,
            skipped_shard_count=skipped_shard_count,
            skipped_prediction_count=skipped_prediction_count,
            skipped_shards=skipped_shards,
            skipped_scene_ids=skipped_scene_ids,
            warnings=warnings,
        )

    return await write_final_evaluation_manifest(
        request=request,
        prediction_manifest=prediction_manifest,
        accumulator=accumulator,
        evaluated_sample_count=evaluated_sample_count,
        evaluation_unit="annotation",
        metadata={
            "missing_gt_policy": _policy_value(request.missing_gt_policy),
            "scene_index": _scene_index_summary(scene_index),
            "skipped_shard_count": skipped_shard_count,
            "skipped_prediction_count": skipped_prediction_count,
            "skipped_shards": skipped_shards[:100],
            "skipped_scene_ids": skipped_scene_ids,
            "warnings": warnings[:100],
            "evaluated_scene_ids": sorted(evaluated_scene_ids),
        },
    )


def evaluate_center_distance_sample_payload(
    *,
    sample_payload: dict[str, Any],
    sample_index: dict[str, SceneSampleManifest],
    match_distance_m: float,
) -> dict[str, Any]:
    """Run center-distance matching for one sample payload."""

    sample_id = sample_payload["sample_id"]
    sample_manifest = sample_index.get(sample_id)

    if sample_manifest is None:
        raise FileNotFoundError(
            f"Sample manifest not found for sample_id: {sample_id!r}"
        )

    return utils.evaluate_sample(
        sample=sample_manifest,
        predictions=sample_payload.get("predictions", []),
        match_distance_m=match_distance_m,
        dataset_id=sample_payload.get("dataset_id"),
        dataset_version=sample_payload.get("dataset_version"),
    )


async def _handle_missing_dataset_gt(
    *,
    request: DetectionEvaluationRequest,
    prediction_manifest: Any,
    scene_index: Any,
) -> DetectionEvaluationResult:
    reason = (
        "No ground-truth annotations were found in the selected dataset scenes. "
        "Detection evaluation was skipped."
    )

    _raise_if_missing_gt_policy_fail(
        request=request,
        reason=reason,
    )

    return await write_skipped_evaluation_manifest(
        request=request,
        prediction_manifest=prediction_manifest,
        reason=reason,
        metadata={
            "missing_gt_policy": _policy_value(request.missing_gt_policy),
            "scene_index": _scene_index_summary(scene_index),
            "skipped_prediction_shard_count": len(
                prediction_manifest.prediction_shards
            ),
            "skipped_prediction_count": _prediction_count_from_shards(
                prediction_manifest.prediction_shards
            ),
        },
    )


async def _handle_no_evaluable_shards(
    *,
    request: DetectionEvaluationRequest,
    prediction_manifest: Any,
    scene_index: Any,
    skipped_shard_count: int,
    skipped_prediction_count: int,
    skipped_shards: list[dict[str, Any]],
    skipped_scene_ids: list[str],
    warnings: list[dict[str, Any]],
) -> DetectionEvaluationResult:
    reason = (
        "No prediction shards were evaluable against ground-truth scenes. "
        "Detection evaluation was skipped."
    )

    _raise_if_missing_gt_policy_fail(
        request=request,
        reason=reason,
    )

    return await write_skipped_evaluation_manifest(
        request=request,
        prediction_manifest=prediction_manifest,
        reason=reason,
        metadata={
            "missing_gt_policy": _policy_value(request.missing_gt_policy),
            "scene_index": _scene_index_summary(scene_index),
            "skipped_shard_count": skipped_shard_count,
            "skipped_prediction_count": skipped_prediction_count,
            "skipped_shards": skipped_shards[:100],
            "skipped_scene_ids": skipped_scene_ids,
            "warnings": warnings[:100],
        },
    )


def _raise_if_missing_gt_policy_fail(
    *,
    request: DetectionEvaluationRequest,
    reason: str,
) -> None:
    if _missing_gt_policy_is_fail(request.missing_gt_policy):
        raise ValueError(reason)


def _missing_gt_policy_is_fail(policy: Any) -> bool:
    return _policy_value(policy) == "fail"


def _policy_value(policy: Any) -> str:
    value = getattr(policy, "value", policy)
    return str(value).lower()


def _scene_index_summary(scene_index: EvaluationSceneIndex) -> dict[str, Any]:
    return {
        "scene_count": scene_index.scene_count,
        "sample_count": scene_index.sample_count,
        "frame_count": scene_index.frame_count,
        "annotation_count": scene_index.annotation_count,
        "ground_truth_scene_count": scene_index.ground_truth_scene_count,
        "scenes": [
            {
                "scene_id": scene.scene_id,
                "sample_count": scene.sample_count,
                "frame_count": scene.frame_count,
                "annotation_count": scene.annotation_count,
                "has_ground_truth": scene.has_ground_truth,
                "ground_truth_source": scene.ground_truth_source,
            }
            for scene in scene_index.scenes
        ],
    }


def _prediction_count_from_shards(shards: list[Any]) -> int:
    return sum(int(shard.prediction_count or 0) for shard in shards)
