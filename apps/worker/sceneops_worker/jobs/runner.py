from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import (
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobStepStatus,
)
from sceneops_core.time import utc_now
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.context import JobContext
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)


class JobRunner:
    def __init__(
        self,
        *,
        context: JobContext,
        handler_registry: JobHandlerRegistry | None = None,
    ) -> None:
        self.job_store = context.job_store
        self.job_event_store = context.job_event_store
        self.context = context
        self.worker_id = context.worker_id
        self.handler_registry = (
            handler_registry or create_default_job_handler_registry()
        )

    async def run(self, job_id: str) -> JobManifest:
        job = await self._load_job(job_id)

        self._validate_runnable(job)

        job = await self._mark_job_running(job)

        await self._append_job_started_event(job)

        running_step_name = self._get_running_step_name(job)

        try:
            if running_step_name is not None:
                await self._append_step_started_event(
                    job=job,
                    step_name=running_step_name,
                )

            result = await self._execute_job(job)
            result_payload = self._to_result_payload(result)

            if running_step_name is not None:
                await self._append_step_succeeded_event(
                    job=job,
                    step_name=running_step_name,
                )

            job = await self._mark_job_succeeded(
                job,
                result=result_payload,
            )

            await self._append_job_succeeded_event(
                job=job,
                result=result_payload,
            )

            return job

        except Exception as error:
            if running_step_name is not None:
                await self._append_step_failed_event(
                    job=job,
                    step_name=running_step_name,
                    error=error,
                )

            job = await self._mark_job_failed(
                job,
                error=ErrorInfo(
                    type=error.__class__.__name__,
                    message=str(error),
                ),
            )

            await self._append_job_failed_event(
                job=job,
                error=error,
            )

            raise

    async def _load_job(self, job_id: str) -> JobManifest:
        job = await self.job_store.get_job(job_id)

        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        return job

    def _validate_runnable(self, job: JobManifest) -> None:
        if job.status == JobStatus.SUCCEEDED:
            raise RuntimeError(f"Job is already succeeded: {job.job_id}")

        if job.status == JobStatus.RUNNING:
            raise RuntimeError(f"Job is already running: {job.job_id}")

        if job.status == JobStatus.CANCELED:
            raise RuntimeError(f"Job is canceled: {job.job_id}")

    async def _execute_job(self, job: JobManifest) -> BaseModel:
        handler = self.handler_registry.get(job.type)

        try:
            params = handler.params_model.model_validate(job.params)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid params for job {job.job_id} " f"of type {job.type}: {exc}"
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

        return await self.job_store.save_job(job)

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

        return await self.job_store.save_job(job)

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

        return await self.job_store.save_job(job)

    def _mark_first_pending_step_running(self, job: JobManifest) -> None:
        now = utc_now()

        for step in job.steps:
            if step.status == JobStepStatus.PENDING:
                step.status = JobStepStatus.RUNNING
                step.started_at = step.started_at or now
                return

    def _get_running_step_name(self, job: JobManifest) -> str | None:
        for step in job.steps:
            if step.status == JobStepStatus.RUNNING:
                return getattr(step, "name", "unknown")

        return None

    async def _append_job_started_event(self, job: JobManifest) -> None:
        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.JOB_STARTED,
            message="Job started",
            payload={
                "worker_id": self.worker_id,
                "job_type": self._enum_value(job.type),
            },
        )

    async def _append_job_succeeded_event(
        self,
        *,
        job: JobManifest,
        result: dict[str, Any],
    ) -> None:
        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.JOB_SUCCEEDED,
            message="Job succeeded",
            payload={
                "result": result,
            },
        )

    async def _append_job_failed_event(
        self,
        *,
        job: JobManifest,
        error: Exception,
    ) -> None:
        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.JOB_FAILED,
            level=JobEventLevel.ERROR,
            message="Job failed",
            payload={
                "error_type": error.__class__.__name__,
                "error_message": str(error),
            },
        )

    async def _append_step_started_event(
        self,
        *,
        job: JobManifest,
        step_name: str,
    ) -> None:
        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.STEP_STARTED,
            message=f"Step started: {step_name}",
            payload={
                "step": step_name,
            },
        )

    async def _append_step_succeeded_event(
        self,
        *,
        job: JobManifest,
        step_name: str,
    ) -> None:
        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.STEP_SUCCEEDED,
            message=f"Step succeeded: {step_name}",
            payload={
                "step": step_name,
            },
        )

    async def _append_step_failed_event(
        self,
        *,
        job: JobManifest,
        step_name: str,
        error: Exception,
    ) -> None:
        await self.job_event_store.append(
            job_id=job.job_id,
            event_type=JobEventType.STEP_FAILED,
            level=JobEventLevel.ERROR,
            message=f"Step failed: {step_name}",
            payload={
                "step": step_name,
                "error_type": error.__class__.__name__,
                "error_message": str(error),
            },
        )

    def _enum_value(self, value: Any) -> Any:
        if hasattr(value, "value"):
            return value.value

        return value
