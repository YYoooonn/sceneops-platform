from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pydantic import ValidationError

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.execution.dispatcher import ExecutionDispatcher
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.events import JobEventPublisher
from sceneops_worker.jobs.execution import JobExecution
from sceneops_worker.jobs.lease import (
    JobLeaseKeeper,
    LeaseRenewal,
    PostgresLeaseRenewal,
)
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


class JobNotClaimableError(RuntimeError):
    """The Job is not runnable (already running, terminal or cancelled) or another
    claim took it first: the message that asked for this run is a duplicate or
    a late one, and nothing was done. Expected under at-least-once delivery."""


class JobOwnershipLostError(RuntimeError):
    """The Job is no longer RUNNING under this worker's claim: it was abandoned,
    reclaimed after its lease expired, or finished by someone else. The worker
    writes nothing more for it."""


class JobRunner:
    """The single runtime entry point that executes a Job.

    Claims the Job, runs its domain handler and persists the terminal state.
    A handler failure is a persisted ``FAILED`` Job, not an exception: the
    returned Job is the outcome. Only a Job that cannot be claimed (missing,
    already running, already terminal) or that this worker no longer owns
    raises.

    A claim is one ``lease_generation`` of the Job, held by a lease that a
    ``JobLeaseKeeper`` renews until the terminal state is written. A Job leaves
    RUNNING exactly once per claim, written by the worker holding it
    (``JobStore.save_owned``, fenced by the generation). A worker whose claim is
    gone has its handler cancelled and writes nothing more. Once the terminal
    state is committed it is final: the JobEvents recorded afterwards are
    bookkeeping, and a failure to write them is logged without touching the Job.

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
        lease_renewal: Callable[[], LeaseRenewal] = PostgresLeaseRenewal,
        worker_node: str | None = None,
    ) -> None:
        self.context = context
        self.worker_id = context.worker_id
        self.dispatcher = dispatcher
        self.handler_registry = (
            handler_registry or create_default_job_handler_registry()
        )
        self.lease_renewal = lease_renewal
        self.events = JobEventPublisher(
            context.job_event_store,
            worker_id=self.worker_id,
            worker_node=worker_node,
        )
        self.result_recorder = JobResultRecorder()

    async def run(self, job_id: str) -> JobManifest:
        execution = await self._prepare_execution(job_id)
        lease = self._keep_lease(execution)

        try:
            await self._start_job(execution)
            await self._start_step(execution)
            await self._execute_handler(execution, lease)
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

        finally:
            lease.stop()

        await self._record_terminal_events(execution)

        job = execution.job
        if job.pipeline_run_id is not None:
            self.dispatcher.advance_pipeline(job.pipeline_run_id)
        return job

    # ── preparation ───────────────────────────────────────────────────────────

    async def _prepare_execution(self, job_id: str) -> JobExecution:
        job = await self._claim_job(job_id)
        await self.context.commit()
        execution = JobExecution(
            job=job, worker_id=self.worker_id, lease_generation=job.lease_generation
        )

        await self.events.job_locked(execution.job)
        await self.context.commit()

        return execution

    def _keep_lease(self, execution: JobExecution) -> JobLeaseKeeper:
        loop = asyncio.get_running_loop()

        def cancel_handler() -> None:
            task = execution.handler_task
            if task is not None and not task.done():
                task.cancel()

        def on_lost() -> None:
            # Called from the keeper's thread; the handler runs on this loop.
            try:
                loop.call_soon_threadsafe(cancel_handler)
            except RuntimeError:  # the loop is closed: the run is over
                pass

        return JobLeaseKeeper(
            job_id=execution.job.job_id,
            lease_generation=execution.lease_generation,
            lease_seconds=self._lease_seconds,
            renewal_factory=self.lease_renewal,
            on_lost=on_lost,
        ).start()

    @property
    def _lease_seconds(self) -> float:
        return self.context.settings.runtime.job_lease_seconds

    async def _claim_job(self, job_id: str) -> JobManifest:
        claimed = await self.context.job_store.claim_for_run(
            job_id,
            worker_id=self.worker_id,
            runnable_statuses=_RUNNABLE_STATUSES,
            lease_seconds=self._lease_seconds,
        )

        if claimed is not None:
            return claimed

        job = await self._load_job(job_id)

        if job.status in _RUNNABLE_STATUSES:
            raise JobNotClaimableError(
                f"Job could not be claimed, possibly claimed by another worker: "
                f"{job.job_id}, status={job.status.value}"
            )

        self._validate_runnable(job)
        raise JobNotClaimableError(
            f"Job is not runnable: {job.job_id}, status={job.status.value}"
        )

    async def _load_job(self, job_id: str) -> JobManifest:
        job = await self.context.job_store.get(job_id)

        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")

        return job

    def _validate_runnable(self, job: JobManifest) -> None:
        if job.status == JobStatus.SUCCEEDED:
            raise JobNotClaimableError(f"Job is already succeeded: {job.job_id}")

        if job.status == JobStatus.RUNNING:
            raise JobNotClaimableError(f"Job is already running: {job.job_id}")

        if job.status == JobStatus.CANCELLED:
            raise JobNotClaimableError(f"Job is cancelled: {job.job_id}")

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

    async def _execute_handler(
        self, execution: JobExecution, lease: JobLeaseKeeper
    ) -> None:
        handler = self.handler_registry.get(execution.job.type)

        try:
            params = handler.params_model.model_validate(execution.job.params)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid params for job {execution.job.job_id} of type "
                f"{execution.job.type}: {exc}"
            ) from exc

        if lease.lost:
            raise self._ownership_lost(execution, "lost before its handler ran")
        # A task of its own, so losing the lease cancels the handler and nothing
        # else: the claim's own writes are fenced, never interrupted.
        execution.handler_task = asyncio.ensure_future(
            handler.run(
                JobHandlerRequest(
                    job=execution.job,
                    params=params,
                    context=self.context,
                )
            )
        )
        try:
            result = await execution.handler_task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if lease.lost and not (current is not None and current.cancelling()):
                raise self._ownership_lost(
                    execution, "lost while its handler ran; the handler was cancelled"
                ) from None
            raise
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
            execution.job, lease_generation=execution.lease_generation
        )
        if saved is None:
            raise self._ownership_lost(
                execution, f"refusing to write {execution.job.status.value}"
            )
        return saved

    def _ownership_lost(
        self, execution: JobExecution, detail: str
    ) -> JobOwnershipLostError:
        return JobOwnershipLostError(
            f"Job {execution.job.job_id} is no longer RUNNING under claim "
            f"{execution.lease_generation} of {self.worker_id}: {detail}"
        )

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
