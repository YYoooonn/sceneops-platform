from __future__ import annotations

from enum import StrEnum


class EvaluationTaskType(StrEnum):
    DETECTION = "detection"
    TRACKING = "tracking"
    SEGMENTATION = "segmentation"
    CUSTOM = "custom"
