from __future__ import annotations

from enum import StrEnum


class PipelineType(StrEnum):
    DATASET_INGESTION = "dataset_ingestion"
    DETECTION_VALIDATION = "detection_validation"


class PipelineRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class PipelineStepRunStatus(StrEnum):
    PENDING = "pending"
    WAITING = "waiting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"
