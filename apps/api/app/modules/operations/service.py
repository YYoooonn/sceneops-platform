from __future__ import annotations

from datetime import datetime

from sceneops_core.operations.schemas import (
    JobTimelineEvent,
    JobTimelineResponse,
    JobTimelineStep,
    OperationsSummaryResponse,
    PipelineStepTimelineItem,
    PipelineTimelineResponse,
    RecentFailureItem,
    StatusCount,
)
from sceneops_db.jobs import JobEventRepository, JobRepository
from sceneops_db.pipelines import PipelineRunRepository, PipelineStepRunRepository


class OperationsService:
    def __init__(
        self,
        *,
        job_repository: JobRepository,
        job_event_repository: JobEventRepository,
        pipeline_run_repository: PipelineRunRepository,
        pipeline_step_run_repository: PipelineStepRunRepository,
    ) -> None:
        self.job_repository = job_repository
        self.job_event_repository = job_event_repository
        self.pipeline_run_repository = pipeline_run_repository
        self.pipeline_step_run_repository = pipeline_step_run_repository

    async def get_job_timeline(
        self,
        *,
        job_id: str,
    ) -> JobTimelineResponse:
        job = await self.job_repository.get(job_id)
        events = await self.job_event_repository.list_by_job(job_id=job_id)

        return JobTimelineResponse(
            job_id=job.job_id,
            job_type=job.type,
            status=job.status,
            worker_id=job.worker_id,
            pipeline_run_id=getattr(job, "pipeline_run_id", None),
            pipeline_step_run_id=getattr(job, "pipeline_step_run_id", None),
            pipeline_step_name=getattr(job, "pipeline_step_name", None),
            queued_at=self._dt(job.queued_at),
            locked_at=self._dt(job.locked_at),
            started_at=self._dt(job.started_at),
            heartbeat_at=self._dt(job.heartbeat_at),
            finished_at=self._dt(job.finished_at),
            queue_latency_ms=self._duration_ms(job.queued_at, job.started_at),
            duration_ms=self._duration_ms(job.started_at, job.finished_at),
            total_elapsed_ms=self._duration_ms(job.queued_at, job.finished_at),
            error_type=job.error.type if job.error is not None else None,
            error_message=job.error.message if job.error is not None else None,
            steps=[
                JobTimelineStep(
                    name=getattr(step, "name", "unknown"),
                    status=step.status.value
                    if hasattr(step.status, "value")
                    else str(step.status),
                    started_at=self._dt(step.started_at),
                    finished_at=self._dt(step.finished_at),
                    duration_ms=self._duration_ms(
                        step.started_at,
                        step.finished_at,
                    ),
                )
                for step in job.steps
            ],
            events=[
                JobTimelineEvent(
                    event_id=getattr(event, "event_id", None),
                    event_type=event.event_type,
                    level=event.level,
                    message=event.message,
                    payload=event.payload,
                    created_at=self._dt(event.created_at),
                )
                for event in events
            ],
        )

    async def get_pipeline_timeline(
        self,
        *,
        pipeline_run_id: str,
    ) -> PipelineTimelineResponse:
        pipeline_run = await self.pipeline_run_repository.get(pipeline_run_id)
        steps = await self.pipeline_step_run_repository.list_by_pipeline_run(
            pipeline_run_id=pipeline_run_id
        )

        return PipelineTimelineResponse(
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_type=pipeline_run.type,
            status=pipeline_run.status,
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            model_id=pipeline_run.model_id,
            model_version=pipeline_run.model_version,
            created_at=self._dt(pipeline_run.created_at),
            started_at=self._dt(pipeline_run.started_at),
            finished_at=self._dt(pipeline_run.finished_at),
            queue_latency_ms=self._duration_ms(
                pipeline_run.created_at,
                pipeline_run.started_at,
            ),
            duration_ms=self._duration_ms(
                pipeline_run.started_at,
                pipeline_run.finished_at,
            ),
            total_elapsed_ms=self._duration_ms(
                pipeline_run.created_at,
                pipeline_run.finished_at,
            ),
            error_type=(
                pipeline_run.error.type if pipeline_run.error is not None else None
            ),
            error_message=(
                pipeline_run.error.message if pipeline_run.error is not None else None
            ),
            steps=[
                PipelineStepTimelineItem(
                    pipeline_step_run_id=step.pipeline_step_run_id,
                    step_name=step.step_name,
                    step_order=step.step_order,
                    job_type=step.job_type,
                    job_id=step.job_id,
                    status=step.status,
                    depends_on_step_names=step.depends_on_step_names or [],
                    started_at=self._dt(step.started_at),
                    finished_at=self._dt(step.finished_at),
                    duration_ms=self._duration_ms(
                        step.started_at,
                        step.finished_at,
                    ),
                    error_type=step.error.type if step.error is not None else None,
                    error_message=(
                        step.error.message if step.error is not None else None
                    ),
                )
                for step in steps
            ],
        )

    async def get_summary(
        self,
        *,
        recent_failure_limit: int = 10,
    ) -> OperationsSummaryResponse:
        job_counts = await self.job_repository.count_by_status()
        pipeline_counts = await self.pipeline_run_repository.count_by_status()

        failed_jobs = await self.job_repository.list_recent_failures(
            limit=recent_failure_limit
        )
        failed_pipelines = await self.pipeline_run_repository.list_recent_failures(
            limit=recent_failure_limit
        )

        recent_failures = [
            *[
                RecentFailureItem(
                    resource_type="job",
                    resource_id=job.job_id,
                    resource_kind=job.type.value
                    if hasattr(job.type, "value")
                    else str(job.type),
                    status=job.status.value
                    if hasattr(job.status, "value")
                    else str(job.status),
                    error_type=job.error.type if job.error is not None else None,
                    error_message=job.error.message if job.error is not None else None,
                    created_at=self._dt(job.created_at),
                    started_at=self._dt(job.started_at),
                    finished_at=self._dt(job.finished_at),
                    updated_at=self._dt(job.updated_at),
                )
                for job in failed_jobs
            ],
            *[
                RecentFailureItem(
                    resource_type="pipeline",
                    resource_id=pipeline.pipeline_run_id,
                    resource_kind=pipeline.type.value
                    if hasattr(pipeline.type, "value")
                    else str(pipeline.type),
                    status=pipeline.status.value
                    if hasattr(pipeline.status, "value")
                    else str(pipeline.status),
                    error_type=(
                        pipeline.error.type if pipeline.error is not None else None
                    ),
                    error_message=(
                        pipeline.error.message if pipeline.error is not None else None
                    ),
                    created_at=self._dt(pipeline.created_at),
                    started_at=self._dt(pipeline.started_at),
                    finished_at=self._dt(pipeline.finished_at),
                    updated_at=self._dt(pipeline.updated_at),
                )
                for pipeline in failed_pipelines
            ],
        ]

        recent_failures = sorted(
            recent_failures,
            key=lambda item: item.updated_at or item.created_at or "",
            reverse=True,
        )[:recent_failure_limit]

        return OperationsSummaryResponse(
            jobs=[
                StatusCount(status=status, count=count)
                for status, count in sorted(job_counts.items())
            ],
            pipelines=[
                StatusCount(status=status, count=count)
                for status, count in sorted(pipeline_counts.items())
            ],
            recent_failures=recent_failures,
        )

    def _dt(self, value: datetime | str | None) -> str | None:
        if value is None:
            return None

        if isinstance(value, str):
            return value

        return value.isoformat()

    def _duration_ms(
        self,
        start: datetime | str | None,
        end: datetime | str | None,
    ) -> int | None:
        start_dt = self._parse_dt(start)
        end_dt = self._parse_dt(end)

        if start_dt is None or end_dt is None:
            return None

        return int((end_dt - start_dt).total_seconds() * 1000)

    def _parse_dt(self, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
