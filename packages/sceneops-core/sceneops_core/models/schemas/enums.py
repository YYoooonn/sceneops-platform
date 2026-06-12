from __future__ import annotations

from enum import StrEnum


class ModelBackend(StrEnum):
    MOCK = "mock"
    ONNX_RUNTIME = "onnx_runtime"
    GROUNDING_DINO = "grounding_dino"
    TRITON = "triton"
    REMOTE_HTTP = "remote_http"


class ModelVersionStatus(StrEnum):
    REGISTERED = "registered"
    READY = "ready"
    DEPRECATED = "deprecated"
    FAILED = "failed"


class ModelTaskType(StrEnum):
    DETECTION = "detection"
    SEGMENTATION = "segmentation"
    TRACKING = "tracking"
    SCENE_UNDERSTANDING = "scene_understanding"
