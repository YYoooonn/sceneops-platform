from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from sceneops_core.common.ids import generate_job_event_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobStepStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)


def _build_job_event(
    *,
    job: JobManifest,
    event_type: JobEventType,
    worker_id: str | None = None,
    level: JobEventLevel = JobEventLevel.INFO,
    status: JobStatus | None = None,
    job_step_id: str | None,
    job_step_name: str | None = None,
    job_step_status: JobStepStatus | None = None,
    message: str | None = None,
    error: ErrorInfo | None = None,
    data: dict[str, Any] | None = None,
) -> JobEvent:
    return JobEvent(
        event_id=generate_job_event_id(),
        job_id=job.job_id,
        type=event_type,
        job_type=job.type,
        level=level,
        status=status,
        job_step_id=job_step_id,
        job_step_name=job_step_name,
        job_step_status=job_step_status,
        pipeline_run_id=job.pipeline_run_id,
        pipeline_task_run_id=job.pipeline_task_run_id,
        pipeline_task_id=job.pipeline_task_id,
        worker_id=worker_id,
        message=message,
        error=error,
        data=data or {},
        created_at=utc_now(),
    )


class JobRunner:
    def __init__(
        self,
        context: WorkerContext,
        handler_registry: JobHandlerRegistry | None = None,
    ) -> None:
        self.context = context
        self.worker_id = context.worker_id
        self.handler_registry = (
            handler_registry or create_default_job_handler_registry()
        )

    async def run(self, job_id: str) -> JobManifest:
        job = await self._load_job(job_id)

        self._validate_runnable(job)

        job = await self._mark_job_running(job)
        await self.context.commit()

        await self._append_event(
            job=job,
            event_type=JobEventType.STARTED,
            status=JobStatus.RUNNING,
            message="Job started",
            data={"worker_id": self.worker_id, "job_type": job.type.value},
        )
        await self.context.commit()

        running_step = self._get_running_step(job)
        running_step_id, running_step_name = (
            running_step if running_step is not None else (None, None)
        )

        try:
            if running_step is not None:
                await self._append_event(
                    job=job,
                    event_type=JobEventType.STEP_STARTED,
                    job_step_id=running_step_id,
                    job_step_name=running_step_name,
                    job_step_status=JobStepStatus.RUNNING,
                    message=f"Step started: {running_step_name}",
                )
                await self.context.commit()

            result = await self._execute_job(job)
            result_payload = self._to_result_payload(result)

            if running_step is not None:
                await self._append_event(
                    job=job,
                    event_type=JobEventType.STEP_SUCCEEDED,
                    job_step_id=running_step_id,
                    job_step_name=running_step_name,
                    job_step_status=JobStepStatus.SUCCEEDED,
                    message=f"Step succeeded: {running_step_name}",
                )
                await self.context.commit()

            job = await self._mark_job_succeeded(job, result=result_payload)
            await self.context.commit()

            await self._append_event(
                job=job,
                event_type=JobEventType.SUCCEEDED,
                status=JobStatus.SUCCEEDED,
                message="Job succeeded",
            )
            await self.context.commit()

            return job

        except Exception as error:
            await self.context.rollback()

            if running_step is not None:
                await self._append_event(
                    job=job,
                    event_type=JobEventType.STEP_FAILED,
                    level=JobEventLevel.ERROR,
                    job_step_id=running_step_id,
                    job_step_name=running_step_name,
                    job_step_status=JobStepStatus.FAILED,
                    message=f"Step failed: {running_step_name}",
                    error=ErrorInfo(
                        type=error.__class__.__name__,
                        message=str(error),
                    ),
                )

            job = await self._mark_job_failed(
                job,
                error=ErrorInfo(
                    type=error.__class__.__name__,
                    message=str(error),
                ),
            )

            await self._append_event(
                job=job,
                event_type=JobEventType.FAILED,
                level=JobEventLevel.ERROR,
                status=JobStatus.FAILED,
                message="Job failed",
                error=ErrorInfo(
                    type=error.__class__.__name__,
                    message=str(error),
                ),
            )
            await self.context.commit()

            raise

    async def _load_job(self, job_id: str) -> JobManifest:
        job = await self.context.job_store.get(job_id)

        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        return job

    def _validate_runnable(self, job: JobManifest) -> None:
        if job.status == JobStatus.SUCCEEDED:
            raise RuntimeError(f"Job is already succeeded: {job.job_id}")

        if job.status == JobStatus.RUNNING:
            raise RuntimeError(f"Job is already running: {job.job_id}")

        if job.status == JobStatus.CANCELLED:
            raise RuntimeError(f"Job is cancelled: {job.job_id}")

    async def _execute_job(self, job: JobManifest) -> BaseModel:
        handler = self.handler_registry.get(job.type)

        try:
            params = handler.params_model.model_validate(job.params)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid params for job {job.job_id} of type {job.type}: {exc}"
            ) from exc

        return await handler.run(
            JobHandlerRequest(
                job=job,
                params=params,
                context=self.context,
            )
        )

    def _to_result_payload(self, result: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")

        return result

    async def _mark_job_running(self, job: JobManifest) -> JobManifest:
        now = utc_now()

        job.status = JobStatus.RUNNING
        job.worker_id = self.worker_id
        job.locked_at = now
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.updated_at = now
        job.finished_at = None
        job.error = None

        self._mark_first_pending_step_running(job)

        return await self.context.job_store.save(job)

    async def _mark_job_succeeded(
        self,
        job: JobManifest,
        *,
        result: dict[str, Any],
    ) -> JobManifest:
        now = utc_now()

        for step in job.steps:
            if step.status in {JobStepStatus.PENDING, JobStepStatus.RUNNING}:
                step.status = JobStepStatus.SUCCEEDED
                step.started_at = step.started_at or job.started_at or now
                step.finished_at = step.finished_at or now

        job.status = JobStatus.SUCCEEDED
        job.result = result
        job.error = None
        job.heartbeat_at = now
        job.finished_at = now
        job.updated_at = now

        return await self.context.job_store.save(job)

    async def _mark_job_failed(
        self,
        job: JobManifest,
        *,
        error: ErrorInfo,
    ) -> JobManifest:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                step.status = JobStepStatus.FAILED
                step.finished_at = now

        job.status = JobStatus.FAILED
        job.error = error
        job.heartbeat_at = now
        job.finished_at = now
        job.updated_at = now

        return await self.context.job_store.save(job)

    def _mark_first_pending_step_running(self, job: JobManifest) -> None:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.PENDING:
                step.status = JobStepStatus.RUNNING
                step.started_at = step.started_at or now
                return

    def _get_running_step(self, job: JobManifest) -> tuple[str, str] | None:
        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                return step.job_step_id, step.job_step_name

        return None

    async def _append_event(
        self,
        *,
        job: JobManifest,
        event_type: JobEventType,
        level: JobEventLevel = JobEventLevel.INFO,
        status: JobStatus | None = None,
        job_step_id: str | None = None,
        job_step_name: str | None = None,
        job_step_status: JobStepStatus | None = None,
        message: str | None = None,
        error: ErrorInfo | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = _build_job_event(
            job=job,
            event_type=event_type,
            worker_id=self.worker_id,
            level=level,
            status=status,
            job_step_id=job_step_id,
            job_step_name=job_step_name,
            job_step_status=job_step_status,
            message=message,
            error=error,
            data=data,
        )
        await self.context.job_event_store.append(event)
