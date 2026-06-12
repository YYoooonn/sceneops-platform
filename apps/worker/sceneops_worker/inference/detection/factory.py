from __future__ import annotations

from collections.abc import Callable

from sceneops_core.inference.enums import InferenceBackendType
from sceneops_worker.inference.detection.base import DetectionInferenceBackend
from sceneops_worker.inference.detection.grounding_dino import (
    GroundingDinoDetectionBackend,
)
from sceneops_worker.inference.detection.mock import MockDetectionInferenceBackend
from sceneops_worker.inference.detection.onnx_runtime import (
    OnnxRuntimeDetectionInferenceBackend,
)

_BACKEND_REGISTRY: dict[
    InferenceBackendType,
    Callable[[], DetectionInferenceBackend],
] = {
    InferenceBackendType.MOCK: MockDetectionInferenceBackend,
    InferenceBackendType.ONNX_RUNTIME: OnnxRuntimeDetectionInferenceBackend,
    InferenceBackendType.GROUNDING_DINO: GroundingDinoDetectionBackend,
}


def register_detection_inference_backend(
    backend_type: InferenceBackendType,
    factory: Callable[[], DetectionInferenceBackend],
) -> None:
    """Register a detection inference backend factory.

    Call this at import time in the backend module to avoid modifying this file.
    Raises ValueError if the backend type is already registered.
    """
    if backend_type in _BACKEND_REGISTRY:
        raise ValueError(
            f"Detection inference backend already registered: {backend_type}"
        )
    _BACKEND_REGISTRY[backend_type] = factory


def create_detection_inference_backend(
    backend: InferenceBackendType,
) -> DetectionInferenceBackend:
    try:
        backend_cls = _BACKEND_REGISTRY[backend]
    except KeyError as exc:
        raise ValueError(f"Unsupported detection inference backend: {backend}") from exc

    return backend_cls()
