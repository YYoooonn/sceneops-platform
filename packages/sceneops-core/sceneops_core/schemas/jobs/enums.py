from __future__ import annotations

from enum import Enum


class JobType(str, Enum):
    INGEST_NUSCENES = "ingest_nuscenes"
    PREDICT_MOCK_DETECTION = "predict_mock_detection"
    EVALUATE_DETECTION = "evaluate_detection"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class JobStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
