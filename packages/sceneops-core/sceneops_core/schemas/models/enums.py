from __future__ import annotations

from enum import StrEnum


class ModelBackend(StrEnum):
    MOCK = "mock"
    ONNX_RUNTIME = "onnx_runtime"
    TRITON = "triton"
    REMOTE_HTTP = "remote_http"


class ModelVersionStatus(StrEnum):
    REGISTERED = "registered"
    READY = "ready"
    DEPRECATED = "deprecated"
    FAILED = "failed"
