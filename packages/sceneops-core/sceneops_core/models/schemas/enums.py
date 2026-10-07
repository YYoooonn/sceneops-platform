from __future__ import annotations

from enum import StrEnum


class ModelBackend(StrEnum):
    MOCK = "mock"
    GROUNDING_DINO = "grounding_dino"


class ModelVersionStatus(StrEnum):
    REGISTERED = "registered"
    READY = "ready"
    DEPRECATED = "deprecated"
    FAILED = "failed"


class ModelTaskType(StrEnum):
    DETECTION = "detection"
    SEGMENTATION = "segmentation"
    TRACKING = "tracking"
