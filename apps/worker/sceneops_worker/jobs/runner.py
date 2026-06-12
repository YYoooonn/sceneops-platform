from __future__ import annotations

from pydantic import ValidationError

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.events import JobEventPublisher
from sceneops_worker.jobs.execution import JobExecution
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)
from sceneops_worker.jobs.result_recorder import JobResultRecorder


_RUNNABLE_STATUSES = {
    JobStatus.PENDING,
    JobStatus.QUEUED,
    # JobStatus.FAILED,
}


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
        self.events = JobEventPublisher(
            context.job_event_store,
            worker_id=self.worker_id,
        )
        self.result_recorder = JobResultRecorder()

    async def run(self, job_id: str) -> JobManifest:
        execution = await self._prepare_execution(job_id)

        try:
            await self._start_job(execution)
            await self._start_step(execution)
            await self._execute_handler(execution)
            await self._complete_job(execution)
            return execution.job

        except Exception as error:
            await self.context.rollback()
            await self._fail_execution(execution, error)
            raise

    # ── preparation ───────────────────────────────────────────────────────────

    async def _prepare_execution(self, job_id: str) -> JobExecution:
        job = await self._claim_job(job_id)
        await self.context.commit()
        execution = JobExecution(job=job, worker_id=self.worker_id)

        await self.events.job_locked(execution.job)
        await self.context.commit()

        return execution

    async def _claim_job(self, job_id: str) -> JobManifest:
        claimed = await self.context.job_store.claim_for_run(
            job_id,
            worker_id=self.worker_id,
            runnable_statuses=_RUNNABLE_STATUSES,
        )

        if claimed is not None:
            return claimed

        job = await self._load_job(job_id)

        if job.status in _RUNNABLE_STATUSES:
            raise RuntimeError(
                f"Job could not be claimed, possibly claimed by another worker: "
                f"{job.job_id}, status={job.status.value}"
            )

        self._validate_runnable(job)
        raise RuntimeError(
            f"Job is not runnable: {job.job_id}, status={job.status.value}"
        )

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

    # ── lifecycle steps ───────────────────────────────────────────────────────

    async def _start_job(self, execution: JobExecution) -> None:
        self.result_recorder.mark_job_running(
            execution.job,
            worker_id=self.worker_id,
        )

        saved_job = await self.context.job_store.save(execution.job)
        await self.context.commit()

        execution.update_job(saved_job)

        await self.events.job_started(execution.job)
        await self.context.commit()

        running_step = self.result_recorder.get_running_step(execution.job)
        step_id, step_name = running_step if running_step is not None else (None, None)
        execution.update_running_step(step_id=step_id, step_name=step_name)

    async def _start_step(self, execution: JobExecution) -> None:
        await self.events.step_started(
            execution.job,
            step_id=execution.running_step_id,
            step_name=execution.running_step_name,
        )
        await self.context.commit()

    async def _execute_handler(self, execution: JobExecution) -> None:
        handler = self.handler_registry.get(execution.job.type)

        try:
            params = handler.params_model.model_validate(execution.job.params)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid params for job {execution.job.job_id} of type "
                f"{execution.job.type}: {exc}"
            ) from exc

        result = await handler.run(
            JobHandlerRequest(
                job=execution.job,
                params=params,
                context=self.context,
            )
        )
        execution.update_handler_result(
            result,
            self.result_recorder.to_payload(result),
        )

    async def _complete_job(self, execution: JobExecution) -> None:
        self.result_recorder.mark_job_succeeded(
            execution.job,
            result=execution.result_payload or {},
        )

        saved_job = await self.context.job_store.save(execution.job)
        await self.context.commit()

        execution.update_job(saved_job)
        await self.events.step_succeeded(
            execution.job,
            step_id=execution.running_step_id,
            step_name=execution.running_step_name,
        )
        await self.events.job_succeeded(execution.job)
        await self.context.commit()

    async def _fail_execution(
        self,
        execution: JobExecution,
        error: Exception,
    ) -> None:
        error_info = self._error_info(error)

        self.result_recorder.mark_job_failed(execution.job, error=error_info)

        failed_job = await self.context.job_store.save(execution.job)
        await self.context.commit()

        execution.update_job(failed_job)

        await self.events.step_failed(
            execution.job,
            step_id=execution.running_step_id,
            step_name=execution.running_step_name,
            error=error_info,
        )
        await self.events.job_failed(execution.job, error=error_info)
        await self.context.commit()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _error_info(self, error: Exception) -> ErrorInfo:
        return ErrorInfo(type=error.__class__.__name__, message=str(error))
