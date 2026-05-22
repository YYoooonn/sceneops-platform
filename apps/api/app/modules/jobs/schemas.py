from sceneops_core.schemas.jobs import *  # noqa: F403

# from datetime import UTC, datetime
# from enum import Enum
# from typing import Any

# from pydantic import BaseModel, Field


# class JobType(str, Enum):
#     INGEST_NUSCENES = "INGEST_NUSCENES"
#     PREDICT_MOCK_DETECTION = "PREDICT_MOCK_DETECTION"
#     EVALUATE_DETECTION = "EVALUATE_DETECTION"


# class JobStatus(str, Enum):
#     PENDING = "PENDING"
#     RUNNING = "RUNNING"
#     SUCCEEDED = "SUCCEEDED"
#     FAILED = "FAILED"
#     CANCELED = "CANCELED"


# class JobStepStatus(str, Enum):
#     PENDING = "PENDING"
#     RUNNING = "RUNNING"
#     SUCCEEDED = "SUCCEEDED"
#     FAILED = "FAILED"
#     SKIPPED = "SKIPPED"


# class JobStep(BaseModel):
#     name: str
#     status: JobStepStatus = JobStepStatus.PENDING
#     message: str | None = None
#     startedAt: str | None = None
#     finishedAt: str | None = None


# class CreateJobRequest(BaseModel):
#     type: JobType
#     datasetId: str | None = None
#     datasetVersion: str | None = None
#     params: dict[str, Any] = Field(default_factory=dict)


# class JobManifest(BaseModel):
#     jobId: str
#     type: JobType
#     status: JobStatus
#     datasetId: str | None = None
#     datasetVersion: str | None = None

#     params: dict[str, Any] = Field(default_factory=dict)
#     steps: list[JobStep] = Field(default_factory=list)

#     result: dict[str, Any] | None = None
#     error: dict[str, Any] | None = None

#     createdAt: str
#     updatedAt: str
#     finishedAt: str | None = None
#     startedAt: str | None = None


# class JobListResponse(BaseModel):
#     jobs: list[JobManifest]
#     count: int


# def utc_now_iso() -> str:
#     return datetime.now(UTC).isoformat()
