from __future__ import annotations

from collections.abc import Callable

from sceneops_worker.evaluation.detection import (
    CenterDistanceDetectionEvaluator,
    DetectionEvaluator,
)

_EVALUATOR_REGISTRY: dict[str, Callable[[], DetectionEvaluator]] = {
    "center-distance": CenterDistanceDetectionEvaluator,
}


def register_detection_evaluator(
    evaluator_id: str,
    factory: Callable[[], DetectionEvaluator],
) -> None:
    """Register a detection evaluator factory.

    Call this at import time in the evaluator module to avoid modifying this file.
    Raises ValueError if the evaluator_id is already registered.
    """
    if evaluator_id in _EVALUATOR_REGISTRY:
        raise ValueError(f"Detection evaluator already registered: {evaluator_id}")
    _EVALUATOR_REGISTRY[evaluator_id] = factory


def create_detection_evaluator(evaluator_id: str) -> DetectionEvaluator:
    try:
        evaluator_cls = _EVALUATOR_REGISTRY[evaluator_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported detection evaluator: {evaluator_id}") from exc

    return evaluator_cls()
