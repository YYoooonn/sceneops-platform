"""Center-distance detection evaluator.

This module is the only one in the detection evaluation package that is
specific to the center-distance matching algorithm.

To add a new evaluator (e.g. IoU-3D), create a peer module (e.g. iou_3d.py)
that follows the same pattern:
  1. Implement ``run()`` that calls the common loaders, creates an accumulator,
     and delegates sample-level work to an algorithm-specific function.
  2. Register via ``register_detection_evaluator`` at import time.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_worker.evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_worker.evaluation.detection.artifacts import (
    write_final_evaluation_manifest,
    write_sample_evaluation,
)
from sceneops_worker.evaluation.detection.base import (
    DetectionEvaluationRequest,
    DetectionEvaluationResult,
    DetectionEvaluator,
)
from sceneops_worker.evaluation.detection.loading import (
    build_sample_index,
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
    """Orchestrate center-distance evaluation for an entire inference run."""
    prediction_manifest = await load_prediction_manifest(request)

    sample_index = await build_sample_index(
        dataset_manifest=request.dataset_manifest,
        scene_artifact_store=request.scene_artifact_store,
    )

    accumulator = EvaluationAccumulator()

    for shard in prediction_manifest.prediction_shards:
        sample_payload = await load_sample_prediction_payload(
            run_artifact_store=request.run_artifact_store,
            uri=shard.uri,
        )

        sample_eval = evaluate_center_distance_sample_payload(
            sample_payload=sample_payload,
            sample_index=sample_index,
            match_distance_m=request.match_distance_m,
        )

        accumulator.add(sample_eval)

        await write_sample_evaluation(
            run_artifact_store=request.run_artifact_store,
            evaluation_run_id=request.evaluation_run_id,
            sample_id=sample_payload["sample_id"],
            sample_eval=sample_eval,
        )

    return await write_final_evaluation_manifest(
        request=request,
        prediction_manifest=prediction_manifest,
        accumulator=accumulator,
        evaluated_sample_count=len(prediction_manifest.prediction_shards),
        evaluation_unit="annotation",
    )


def evaluate_center_distance_sample_payload(
    *,
    sample_payload: dict[str, Any],
    sample_index: dict[str, SceneSampleManifest],
    match_distance_m: float,
) -> dict[str, Any]:
    """Run center-distance matching for one sample payload.

    This is the only function in this module that encodes the center-distance
    algorithm. Replacing this function (or this module) is all that's needed
    to add a new matching algorithm.
    """
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
