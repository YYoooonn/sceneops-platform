from __future__ import annotations

from enum import StrEnum


class InferenceBackendType(StrEnum):
    MOCK = "mock"
    VLM = "vlm"
    GROUNDING_DINO = "grounding_dino"
    # TRITON = "triton"
