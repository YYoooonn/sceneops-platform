from __future__ import annotations

from enum import StrEnum


class InferenceBackendType(StrEnum):
    MOCK = "mock"
    GROUNDING_DINO = "grounding_dino"
