"""The operations views report only state the platform maintains.

A Job and a PipelineRun carry lifecycle state. An ExecutionRecord is the audit
record of one dispatched message and carries none, so no operations view derives
running / failed / succeeded counts or failures from it.
"""

from __future__ import annotations

from sceneops_core.common.time import utc_now
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType

from app.views.operations.schemas import OperationSummaryResponse
from app.views.operations.service import OperationsService


class _Jobs:
    def __init__(self, jobs: list[JobManifest]) -> None:
        self._jobs = jobs

    async def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for job in self._jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    async def list(self, *, status=None, limit=100, offset=0) -> list[JobManifest]:
        return [j for j in self._jobs if status is None or j.status == status]


class _Pipelines:
    async def count_by_status(self) -> dict[str, int]:
        return {}

    async def list(self, *, status=None, limit=100, offset=0) -> list:
        return []


class _Executions:
    """Only what a dispatch audit record supports: listing by what was sent."""

    def __init__(self, executions: list[ExecutionDispatchResult]) -> None:
        self._executions = executions

    async def list(self, *, limit=100, offset=0) -> list[ExecutionDispatchResult]:
        return self._executions


def _job(job_id: str, status: JobStatus) -> JobManifest:
    now = utc_now()
    return JobManifest(
        job_id=job_id,
        type=JobType.PROFILE_SCENE,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _service(jobs: list[JobManifest], executions: list[ExecutionDispatchResult]):
    return OperationsService(
        job_repository=_Jobs(jobs),
        pipeline_repository=_Pipelines(),
        execution_repository=_Executions(executions),
    )


def _dispatch(job_id: str) -> ExecutionDispatchResult:
    return ExecutionDispatchResult(
        execution_id=f"exec-{job_id}",
        execution_backend=ExecutionBackend.CELERY,
        execution_kind=ExecutionKind.JOB_RUN,
        resource_id=job_id,
    )


def test_a_dispatch_record_carries_no_lifecycle_status() -> None:
    assert "status" not in ExecutionDispatchResult.model_fields
    assert "executions" not in OperationSummaryResponse.model_fields


async def test_the_summary_counts_jobs_and_pipelines_only() -> None:
    service = _service(
        [_job("j1", JobStatus.FAILED), _job("j2", JobStatus.SUCCEEDED)],
        [_dispatch("j1"), _dispatch("j2")],
    )

    summary = await service.get_summary()

    assert set(summary.model_dump()) == {"jobs", "pipelines"}
    assert summary.jobs.failed == 1 and summary.jobs.succeeded == 1
    assert summary.pipelines.failed == 0


async def test_failures_are_the_failed_jobs_and_pipeline_runs() -> None:
    service = _service([_job("j1", JobStatus.FAILED)], [_dispatch("j1")])

    failures = await service.get_failures()

    assert [(f.resource_type, f.resource_id) for f in failures.failures] == [
        ("job", "j1")
    ]


async def test_recent_executions_are_the_dispatch_records() -> None:
    service = _service([], [_dispatch("j1")])

    recent = await service.get_recent_executions()

    assert [e.resource_id for e in recent.executions] == ["j1"]
