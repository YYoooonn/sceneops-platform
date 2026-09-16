from __future__ import annotations

from enum import StrEnum


class DatasetValidationStatus(StrEnum):
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
