from __future__ import annotations

from sceneops_worker.evaluation.detection import (
    CenterDistanceDetectionEvaluator,
    DetectionEvaluator,
)

_EVALUATOR_REGISTRY: dict[str, type[DetectionEvaluator]] = {
    "center-distance": CenterDistanceDetectionEvaluator,
}


def create_detection_evaluator(evaluator_id: str) -> DetectionEvaluator:
    try:
        evaluator_cls = _EVALUATOR_REGISTRY[evaluator_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported detection evaluator: {evaluator_id}") from exc

    return evaluator_cls()
