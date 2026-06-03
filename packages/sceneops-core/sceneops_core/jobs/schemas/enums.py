from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    BUILD_SCENES = "build_scenes"
    INGEST_DATASET = "ingest_dataset"
    VALIDATE_DATASET = "validate_dataset"
    PROFILE_DATASET = "profile_dataset"
    PREDICT_DETECTION = "predict_detection"
    EVALUATE_DETECTION = "evaluate_detection"
    AUTO_LABEL_DATASET = "auto_label_dataset"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class JobStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
