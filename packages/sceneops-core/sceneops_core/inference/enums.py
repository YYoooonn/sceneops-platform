from __future__ import annotations

from enum import StrEnum


class InferenceBackendType(StrEnum):
    MOCK = "mock"
    ONNX_RUNTIME = "onnx_runtime"
    # TRITON = "triton"
