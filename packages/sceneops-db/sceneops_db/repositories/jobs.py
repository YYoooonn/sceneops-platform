from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
)


class JobExecutionKeyInFlightError(ValueError):
    """Another PENDING / QUEUED / RUNNING Job already holds this execution key.

    At most one Job per execution key is in flight; PostgreSQL enforces it with a
    partial unique index, so the error reports a write the database refused,
    whatever an earlier read observed."""

    def __init__(self, execution_key: str | None) -> None:
        super().__init__(
            f"A Job with execution_key={execution_key!r} is already pending, "
            "queued or running"
        )
        self.execution_key = execution_key


@runtime_checkable
class JobRepository(Protocol):
    async def create(self, job: JobManifest) -> JobManifest:
        """Raises ``JobExecutionKeyInFlightError`` when ``job`` is in flight and
        another in-flight Job holds its execution key."""
        ...

    async def get(self, job_id: str) -> JobManifest | None: ...

    async def update(self, job: JobManifest) -> JobManifest:
        """An unconditional whole-row write; never a lifecycle transition."""
        ...

    async def queue_if_unchanged(self, job: JobManifest) -> JobManifest | None:
        """Move the Job to QUEUED iff it still has ``job.status``,
        ``job.retry_count`` and ``job.lease_generation``; None when it changed.
        A FAILED Job is retried
        (retry_count + 1). Raises ``JobExecutionKeyInFlightError`` like
        ``create``; the caller's transaction is then unusable."""
        ...

    async def list(
        self,
        *,
        type: JobType | None = None,
        status: JobStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        pipeline_run_id: str | None = None,
        pipeline_task_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobManifest]: ...

    async def count_by_status(self) -> dict[str, int]: ...

    async def find_by_execution_key(
        self,
        execution_key: str,
        *,
        statuses: set[JobStatus],
    ) -> JobManifest | None: ...

    async def list_for_execution_keys(
        self, execution_keys: Sequence[str], *, type: JobType
    ) -> list[JobManifest]: ...


@runtime_checkable
class JobEventRepository(Protocol):
    async def append(self, event: JobEvent) -> JobEvent: ...

    async def get(self, event_id: str) -> JobEvent | None: ...

    async def list_for_job(
        self,
        job_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]: ...

    async def list_for_pipeline_run(
        self,
        pipeline_run_id: str,
        *,
        level: JobEventLevel | None = None,
        type: JobEventType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobEvent]: ...
