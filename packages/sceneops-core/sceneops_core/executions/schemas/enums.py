from __future__ import annotations

from enum import StrEnum


class ExecutionBackend(StrEnum):
    CELERY = "celery"


class ExecutionKind(StrEnum):
    PIPELINE_RUN = "pipeline_run"
    JOB_RUN = "job_run"
