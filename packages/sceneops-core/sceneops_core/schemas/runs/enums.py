from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class RunType(StrEnum):
    INFERENCE = "inference"
    EVALUATION = "evaluation"
    VALIDATION = "validation"
