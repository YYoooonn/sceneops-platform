from __future__ import annotations

import logging

from pydantic import ValidationError

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.execution.dispatcher import ExecutionDispatcher
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.events import JobEventPublisher
from sceneops_worker.jobs.execution import JobExecution
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)
from sceneops_worker.jobs.result_recorder import JobResultRecorder

logger = logging.getLogger(__name__)

_RUNNABLE_STATUSES = {
    JobStatus.PENDING,
    JobStatus.QUEUED,
}


class JobOwnershipLostError(RuntimeError):
    """The Job is no longer RUNNING under this worker: it was abandoned or
    finished by someone else. The worker writes nothing more for it."""


class JobRunner:
    """The single runtime entry point that executes a Job.

    Claims the Job, runs its domain handler and persists the terminal state.
    A handler failure is a persisted ``FAILED`` Job, not an exception: the
    returned Job is the outcome. Only a Job that cannot be claimed (missing,
    already running, already terminal) or that this worker no longer owns
    raises.

    A Job leaves RUNNING exactly once, written by the worker that claimed it
    (``JobStore.save_owned``). Once that terminal state is committed it is final:
    the JobEvents recorded afterwards are bookkeeping, and a failure to write
    them is logged without touching the Job.

    A Job that belongs to a PipelineRun is reported to the dispatcher once its
    terminal state is committed, so the pipeline's orchestrator observes it and
    takes the next step. Whatever backend runs JobRunner therefore keeps the
    pipeline contract.
    """

    def __init__(
        self,
        context: WorkerContext,
        *,
        dispatcher: ExecutionDispatcher,
        handler_registry: JobHandlerRegistry | None = None,
    ) -> None:
        self.context = context
        self.worker_id = context.worker_id
        self.dispatcher = dispatcher
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

        except JobOwnershipLostError:
            logger.error(
                "job %s (%s) is no longer owned by %s; nothing more is written",
                execution.job.job_id,
                execution.job.type.value,
                self.worker_id,
            )
            await self.context.rollback()
            raise

        except Exception as error:
            logger.exception(
                "job %s (%s) failed", execution.job.job_id, execution.job.type.value
            )
            await self.context.rollback()
            await self._fail_execution(execution, error)

        await self._record_terminal_events(execution)

        job = execution.job
        if job.pipeline_run_id is not None:
            self.dispatcher.advance_pipeline(job.pipeline_run_id)
        return job

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

        saved_job = await self._save_owned(execution)
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

        saved_job = await self._save_owned(execution)
        await self.context.commit()

        execution.update_job(saved_job)

    async def _fail_execution(
        self,
        execution: JobExecution,
        error: Exception,
    ) -> None:
        error_info = self._error_info(error)

        self.result_recorder.mark_job_failed(execution.job, error=error_info)

        failed_job = await self._save_owned(execution)
        await self.context.commit()

        execution.update_job(failed_job)

    async def _save_owned(self, execution: JobExecution) -> JobManifest:
        saved = await self.context.job_store.save_owned(
            execution.job, worker_id=self.worker_id
        )
        if saved is None:
            raise JobOwnershipLostError(
                f"Job {execution.job.job_id} is no longer RUNNING under "
                f"{self.worker_id}; refusing to write {execution.job.status.value}"
            )
        return saved

    async def _record_terminal_events(self, execution: JobExecution) -> None:
        """Log the committed terminal state. Best effort: the Job is already
        final, so a failure here is reported and never changes it."""
        job = execution.job
        try:
            if job.status == JobStatus.SUCCEEDED:
                await self.events.step_succeeded(
                    job,
                    step_id=execution.running_step_id,
                    step_name=execution.running_step_name,
                )
                await self.events.job_succeeded(job)
            else:
                error = job.error or ErrorInfo(type="JobFailed", message="")
                await self.events.step_failed(
                    job,
                    step_id=execution.running_step_id,
                    step_name=execution.running_step_name,
                    error=error,
                )
                await self.events.job_failed(job, error=error)
            await self.context.commit()
        except Exception:
            logger.exception(
                "job %s is %s but its terminal events could not be recorded",
                job.job_id,
                job.status.value,
            )
            try:
                await self.context.rollback()
            except Exception:
                logger.exception(
                    "job %s: rollback after event failure failed", job.job_id
                )

    # ── internal helpers ──────────────────────────────────────────────────────

    def _error_info(self, error: Exception) -> ErrorInfo:
        return ErrorInfo(type=error.__class__.__name__, message=str(error))
