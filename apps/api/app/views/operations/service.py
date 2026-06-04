from __future__ import annotations

from sceneops_core.executions.schemas import ExecutionStatus
from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.pipelines.schemas import PipelineRunStatus
from sceneops_db.repositories.executions import ExecutionRecordRepository
from sceneops_db.repositories.jobs import JobRepository
from sceneops_db.repositories.pipelines import PipelineRunRepository

from app.views.operations.schemas import (
    OperationCountSummary,
    OperationFailure,
    OperationFailuresResponse,
    OperationSummaryResponse,
    OperationTimelineEvent,
    OperationTimelineResponse,
    RecentExecutionsResponse,
    RecentJobsResponse,
    RecentPipelinesResponse,
)

_EXPECTED_JOB_STATUSES = {s.value for s in JobStatus}
_EXPECTED_PIPE_STATUSES = {s.value for s in PipelineRunStatus}
_EXPECTED_EXEC_STATUSES = {s.value for s in ExecutionStatus}


class OperationsService:
    def __init__(
        self,
        *,
        job_repository: JobRepository,
        pipeline_repository: PipelineRunRepository,
        execution_repository: ExecutionRecordRepository,
    ) -> None:
        self._jobs = job_repository
        self._pipelines = pipeline_repository
        self._executions = execution_repository

    async def get_summary(self) -> OperationSummaryResponse:
        job_counts = await self._jobs.count_by_status()
        pipe_counts = await self._pipelines.count_by_status()
        exec_counts = await self._executions.count_by_status()

        return OperationSummaryResponse(
            jobs=OperationCountSummary(
                running=job_counts.get(JobStatus.RUNNING, 0),
                failed=job_counts.get(JobStatus.FAILED, 0),
                succeeded=job_counts.get(JobStatus.SUCCEEDED, 0),
                pending=job_counts.get(JobStatus.PENDING, 0),
            ),
            pipelines=OperationCountSummary(
                running=pipe_counts.get(PipelineRunStatus.RUNNING, 0),
                failed=pipe_counts.get(PipelineRunStatus.FAILED, 0),
                succeeded=pipe_counts.get(PipelineRunStatus.SUCCEEDED, 0),
                pending=pipe_counts.get(PipelineRunStatus.PENDING, 0),
            ),
            executions=OperationCountSummary(
                running=exec_counts.get(ExecutionStatus.RUNNING, 0),
                failed=exec_counts.get(ExecutionStatus.FAILED, 0),
                succeeded=exec_counts.get(ExecutionStatus.SUCCEEDED, 0),
                pending=0,
            ),
        )

    async def get_timeline(self, *, limit: int = 50) -> OperationTimelineResponse:
        jobs = await self._jobs.list(limit=limit)
        pipelines = await self._pipelines.list(limit=limit)

        events: list[OperationTimelineEvent] = []

        for job in jobs:
            events.append(
                OperationTimelineEvent(
                    event_type="job_status",
                    resource_type="job",
                    resource_id=job.job_id,
                    status=job.status,
                    message=f"{job.type} — {job.status}",
                    created_at=job.created_at,
                    metadata={"job_type": job.type, "dataset_id": job.dataset_id},
                )
            )

        for run in pipelines:
            events.append(
                OperationTimelineEvent(
                    event_type="pipeline_status",
                    resource_type="pipeline_run",
                    resource_id=run.pipeline_run_id,
                    status=run.status,
                    message=f"{run.type} — {run.status}",
                    created_at=run.created_at,
                    metadata={"pipeline_type": run.type, "dataset_id": run.dataset_id},
                )
            )

        events.sort(key=lambda e: e.created_at or "", reverse=True)
        events = events[:limit]
        return OperationTimelineResponse(events=events, count=len(events))

    async def get_recent_jobs(
        self, *, limit: int = 50, offset: int = 0
    ) -> RecentJobsResponse:
        jobs = await self._jobs.list(limit=limit, offset=offset)
        return RecentJobsResponse(jobs=jobs, count=len(jobs))

    async def get_recent_pipelines(
        self, *, limit: int = 50, offset: int = 0
    ) -> RecentPipelinesResponse:
        runs = await self._pipelines.list(limit=limit, offset=offset)
        return RecentPipelinesResponse(pipeline_runs=runs, count=len(runs))

    async def get_recent_executions(
        self, *, limit: int = 50, offset: int = 0
    ) -> RecentExecutionsResponse:
        executions = await self._executions.list(limit=limit, offset=offset)
        return RecentExecutionsResponse(executions=executions, count=len(executions))

    async def get_failures(
        self, *, limit: int = 50, offset: int = 0
    ) -> OperationFailuresResponse:
        failed_jobs = await self._jobs.list(
            status=JobStatus.FAILED, limit=limit, offset=offset
        )
        failed_pipelines = await self._pipelines.list(
            status=PipelineRunStatus.FAILED, limit=limit, offset=offset
        )
        failed_executions = await self._executions.list(
            status=ExecutionStatus.FAILED, limit=limit, offset=offset
        )

        failures: list[OperationFailure] = []

        for job in failed_jobs:
            failures.append(
                OperationFailure(
                    resource_type="job",
                    resource_id=job.job_id,
                    status=job.status,
                    error=job.error.model_dump() if job.error else None,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
            )

        for run in failed_pipelines:
            failures.append(
                OperationFailure(
                    resource_type="pipeline_run",
                    resource_id=run.pipeline_run_id,
                    status=run.status,
                    error=run.error.model_dump() if run.error else None,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            )

        for exc in failed_executions:
            failures.append(
                OperationFailure(
                    resource_type="execution",
                    resource_id=exc.execution_id,
                    status=exc.status,
                    created_at=exc.created_at,
                    updated_at=exc.updated_at,
                )
            )

        failures.sort(key=lambda f: f.created_at or "", reverse=True)
        failures = failures[:limit]
        return OperationFailuresResponse(failures=failures, count=len(failures))
