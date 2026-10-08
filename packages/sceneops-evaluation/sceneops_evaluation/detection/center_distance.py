"""Center-distance detection evaluator.

Ground truth is the label set revision the evaluation pins; samples and
their label attachments come from the pinned sample views. Nothing is read
from a Scene.

Evaluation policy:
  - A predicted sample is evaluated only if the pinned label set *covers* it
    (the set annotated an observation the sample holds). A covered sample
    with no labels is a valid negative sample.
  - A predicted sample the label set does not cover is skipped, or fails the
    evaluation under ``MissingGroundTruthPolicy.FAIL``.
  - Predictions and labels must be in the same frame; a mismatch fails.
  - Labels whose category is outside ``categories`` (when given) are ignored,
    and so are predictions of such categories.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.evaluations.schemas import EvaluationSampleShardRef
from sceneops_core.jobs.schemas.params import MissingGroundTruthPolicy
from sceneops_evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_evaluation.detection.artifacts import (
    build_final_evaluation_manifest,
    build_skipped_evaluation_manifest,
    write_sample_evaluation,
)
from sceneops_evaluation.detection.base import (
    DetectionEvaluationRequest,
    DetectionEvaluationResult,
    DetectionEvaluator,
)
from sceneops_evaluation.detection.loading import load_sample_prediction_payload

from . import utils

SKIP_NOT_COVERED = "sample_not_covered_by_label_set"


class CenterDistanceDetectionEvaluator(DetectionEvaluator):
    @property
    def evaluator_id(self) -> str:
        return "center-distance"

    async def run(
        self,
        request: DetectionEvaluationRequest,
    ) -> DetectionEvaluationResult:
        return await evaluate_center_distance_detection(request)


def _policy_is_fail(request: DetectionEvaluationRequest) -> bool:
    return request.missing_gt_policy == MissingGroundTruthPolicy.FAIL


async def evaluate_center_distance_detection(
    request: DetectionEvaluationRequest,
) -> DetectionEvaluationResult:
    labels_by_id = {label.label_id: label for label in request.label_set.labels}
    label_set_id = request.label_set.label_set_id

    accumulator = EvaluationAccumulator()
    sample_shards: list[EvaluationSampleShardRef] = []
    evaluated_scene_ids: set[str] = set()
    skipped_shards: list[dict[str, Any]] = []

    for shard in request.prediction.prediction_shards:
        view = request.views[shard.scene_id]
        sample = view.sample(shard.sample_id)
        assert sample is not None  # guaranteed by the pinned manifest + view
        entry = next(e for e in sample.labels if e.label_set_id == label_set_id)

        if not entry.covered:
            if _policy_is_fail(request):
                raise ValueError(
                    f"predicted sample {shard.scene_id}/{shard.sample_id} is not "
                    f"covered by label set {label_set_id!r}"
                )
            skipped_shards.append(
                {
                    "scene_id": shard.scene_id,
                    "sample_id": shard.sample_id,
                    "uri": shard.uri,
                    "prediction_count": shard.prediction_count,
                    "reason": SKIP_NOT_COVERED,
                }
            )
            continue

        payload = await load_sample_prediction_payload(request, shard)
        predictions = payload["predictions"]
        labels = [labels_by_id[label_id] for label_id in entry.label_ids]
        if request.categories is not None:
            labels = [x for x in labels if x.category in request.categories]
            predictions = [
                p for p in predictions if p["category_name"] in request.categories
            ]

        sample_eval = utils.evaluate_sample(
            scene_id=shard.scene_id,
            sample_id=shard.sample_id,
            labels=labels,
            predictions=predictions,
            match_distance_m=request.match_distance_m,
        )
        accumulator.add(sample_eval)
        evaluated_scene_ids.add(shard.scene_id)
        sample_shards.append(
            await write_sample_evaluation(
                run_artifact_store=request.run_artifact_store,
                evaluation_run_id=request.evaluation_run_id,
                scene_id=shard.scene_id,
                sample_id=shard.sample_id,
                sample_eval=sample_eval,
            )
        )

    summary = {
        "label_set_id": label_set_id,
        "label_set_label_count": len(request.label_set.labels),
        "predicted_sample_count": len(request.prediction.prediction_shards),
        "skipped_shard_count": len(skipped_shards),
        "skipped_prediction_count": sum(s["prediction_count"] for s in skipped_shards),
        "skipped_shards": skipped_shards[:100],
        "skipped_scene_ids": sorted({s["scene_id"] for s in skipped_shards}),
        "categories": sorted(request.categories) if request.categories else None,
        "missing_gt_policy": request.missing_gt_policy.value,
    }

    if not sample_shards:
        reason = (
            "No predicted sample is covered by the pinned label set. "
            "Detection evaluation was skipped."
        )
        if _policy_is_fail(request):
            raise ValueError(reason)
        return build_skipped_evaluation_manifest(
            request=request, reason=reason, metadata=summary
        )

    return build_final_evaluation_manifest(
        request=request,
        accumulator=accumulator,
        sample_shards=sample_shards,
        evaluation_unit="label",
        metadata={**summary, "evaluated_scene_ids": sorted(evaluated_scene_ids)},
    )
