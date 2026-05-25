from __future__ import annotations

from pydantic import BaseModel

from sceneops_core.schemas.jobs.enums import JobStepStatus, JobType
from sceneops_core.constants.jobs import (
    EVALUATE_DETECTION_STEPS,
    INGEST_NUSCENES_STEPS,
    PREDICT_MOCK_DETECTION_STEPS,
)

class JobStep(BaseModel):
    name: str
    status: JobStepStatus = JobStepStatus.PENDING
    startedAt: str | None = None
    finishedAt: str | None = None


def build_default_steps(job_type: JobType) -> list[JobStep]:
    if job_type == JobType.INGEST_DATASET:
        return [JobStep(name=name) for name in INGEST_NUSCENES_STEPS]

    if job_type == JobType.PREDICT_DETECTION:
        return [JobStep(name=name) for name in PREDICT_MOCK_DETECTION_STEPS]

    if job_type == JobType.EVALUATE_DETECTION:
        return [JobStep(name=name) for name in EVALUATE_DETECTION_STEPS]

    return []
