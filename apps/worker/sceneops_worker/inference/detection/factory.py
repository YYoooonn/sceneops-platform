from __future__ import annotations

from collections.abc import Callable

from sceneops_core.jobs.schemas import InferenceBackend
from sceneops_worker.inference.detection.base import DetectionInferenceBackend
from sceneops_worker.inference.detection.mock import MockDetectionInferenceBackend
from sceneops_worker.inference.detection.onnx_runtime import (
    OnnxRuntimeDetectionInferenceBackend,
)


_BACKEND_REGISTRY: dict[
    InferenceBackend,
    Callable[[], DetectionInferenceBackend],
] = {
    InferenceBackend.MOCK: MockDetectionInferenceBackend,
    InferenceBackend.ONNX_RUNTIME: OnnxRuntimeDetectionInferenceBackend,
}


def create_detection_inference_backend(
    backend: InferenceBackend,
) -> DetectionInferenceBackend:
    try:
        backend_cls = _BACKEND_REGISTRY[backend]
    except KeyError as exc:
        raise ValueError(f"Unsupported detection inference backend: {backend}") from exc

    return backend_cls()
