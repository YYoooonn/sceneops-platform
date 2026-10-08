"""PipelineOrchestrator: advances a PipelineRun over its durable state.

A Pipeline is an orchestration unit; its domain work is done only by Jobs. The
orchestrator never runs a handler or a JobRunner: it submits each task's Job to the
job execution backend and returns, and it is invoked again, one short step at a
time, whenever there may be something to do:

    POST /pipelines/runs/{id}/execute      the run was QUEUED: start it
    a pipeline-owned Job became terminal   JobRunner reported it: observe it

One ``advance`` is one transaction-scoped step under a row lock on the PipelineRun,
so concurrent or duplicated steps of one run serialize and converge:

    QUEUED    start: RUNNING; every task that did not succeed or skip goes back to
              PENDING, so a FAILED or BLOCKED run resumes at its first unfinished task
    RUNNING   walk the tasks in definition order:
                SUCCEEDED / SKIPPED      next task
                RUNNING, Job in flight   wait (nothing to do until the Job reports)
                RUNNING, Job terminal    record the result and apply the quality gate
                                         -> SUCCEEDED, BLOCKED (run BLOCKED) or FAILED
                                         (run FAILED)
                PENDING                  skip an optional task without params, or
                                         submit its Job and wait
              no task left: the run SUCCEEDED
    other     nothing to do (not started, terminal, or a late report)

Tasks run one at a time in definition order. A submitted Job is committed before
it is dispatched, so a Job a worker claims always exists; a dispatch that fails
leaves the Job QUEUED and the run waiting on it.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_core.pipelines.builtin import get_pipeline_definition
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskDefinition,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_execution.executions.dispatcher import ExecutionDispatcher
from sceneops_execution.jobs.events import JobEventPublisher
from sceneops_execution.pipelines.errors import PipelineQualityBlocked
from sceneops_worker.pipelines.input_resolver import PipelineInputResolver
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_execution.pipelines.quality_gate import PipelineQualityGate
from sceneops_execution.pipelines.result_builder import (
    build_pipeline_result_from_task_runs,
)
from sceneops_worker.pipelines.result_recorder import PipelineTaskResultRecorder

_DONE = {PipelineTaskRunStatus.SUCCEEDED, PipelineTaskRunStatus.SKIPPED}
_JOB_IN_FLIGHT = {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}


class PipelineOrchestrator:
    def __init__(
        self,
        context: WorkerContext,
        *,
        dispatcher: ExecutionDispatcher,
        planner: PipelineJobPlanner | None = None,
        quality_gate: PipelineQualityGate | None = None,
        input_resolver: PipelineInputResolver | None = None,
        result_recorder: PipelineTaskResultRecorder | None = None,
    ) -> None:
        self._context = context
        self._dispatcher = dispatcher
        self._planner = planner or PipelineJobPlanner()
        self._quality_gate = quality_gate or PipelineQualityGate()
        self._input_resolver = input_resolver or PipelineInputResolver(context)
        self._result_recorder = result_recorder or PipelineTaskResultRecorder(context)
        self._events = JobEventPublisher(
            context.job_event_store, worker_id=context.worker_id
        )

    async def advance(self, pipeline_run_id: str) -> PipelineRunManifest:
        store = self._context.pipeline_store
        run = await store.get_for_update(pipeline_run_id)
        if run is None:
            await self._context.rollback()
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")

        if run.status == PipelineRunStatus.QUEUED:
            run = await self._start(run)
        elif run.status != PipelineRunStatus.RUNNING:
            await self._context.rollback()
            return run

        task_runs = {
            t.pipeline_task_id: t for t in await store.list_tasks(run.pipeline_run_id)
        }
        definition = get_pipeline_definition(run.type)

        for task_definition in sorted(definition.tasks, key=lambda t: t.order):
            task_run = task_runs.get(task_definition.pipeline_task_id)
            if task_run is None:
                return await self._finish(
                    run,
                    PipelineRunStatus.FAILED,
                    ErrorInfo(
                        type="PipelineTaskRunMissing",
                        message=(
                            f"pipeline run {run.pipeline_run_id} has no task run for "
                            f"'{task_definition.pipeline_task_id}'"
                        ),
                    ),
                )

            if task_run.status in _DONE:
                continue

            if task_run.status == PipelineTaskRunStatus.RUNNING:
                job = (
                    await self._context.job_store.get(task_run.job_id)
                    if task_run.job_id
                    else None
                )
                if job is not None and job.status in _JOB_IN_FLIGHT:
                    await self._context.commit()
                    return run
                task_run = await self._observe(run, task_definition, task_run, job)
                if task_run.status == PipelineTaskRunStatus.SUCCEEDED:
                    continue
                return await self._finish(
                    run,
                    PipelineRunStatus.BLOCKED
                    if task_run.status == PipelineTaskRunStatus.BLOCKED
                    else PipelineRunStatus.FAILED,
                    task_run.error,
                )

            if self._is_skipped(run, task_definition):
                await self._skip(task_run)
                continue

            try:
                # A savepoint: a task that cannot be submitted (its inputs do not
                # resolve, its params are invalid) leaves no Job behind, while the
                # run keeps its lock and the transitions of this step.
                async with self._context.session.begin_nested():
                    job = await self._submit(run, task_definition, task_run)
            except Exception as error:
                failed = await self._fail_task(task_run, _error_info(error))
                return await self._finish(run, PipelineRunStatus.FAILED, failed.error)

            await self._context.commit()
            execution = self._dispatcher.dispatch_job(job.job_id)
            await self._context.execution_store.create(execution)
            await self._context.commit()
            return run

        return await self._finish(run, PipelineRunStatus.SUCCEEDED, None)

    # ── run transitions ──────────────────────────────────────────────────────

    async def _start(self, run: PipelineRunManifest) -> PipelineRunManifest:
        now = utc_now()
        for task_run in await self._context.pipeline_store.list_tasks(
            run.pipeline_run_id
        ):
            if (
                task_run.status in _DONE
                or task_run.status == PipelineTaskRunStatus.PENDING
            ):
                continue
            task_run.status = PipelineTaskRunStatus.PENDING
            task_run.job_id = None
            task_run.result = None
            task_run.error = None
            task_run.started_at = None
            task_run.finished_at = None
            task_run.updated_at = now
            await self._context.pipeline_store.save_task(task_run)

        run.status = PipelineRunStatus.RUNNING
        run.started_at = run.started_at or now
        run.finished_at = None
        run.error = None
        run.updated_at = now
        return await self._context.pipeline_store.save(run)

    async def _finish(
        self,
        run: PipelineRunManifest,
        status: PipelineRunStatus,
        error: ErrorInfo | None,
    ) -> PipelineRunManifest:
        now = utc_now()
        task_runs = await self._context.pipeline_store.list_tasks(run.pipeline_run_id)
        run.status = status
        run.error = error
        run.finished_at = now
        run.updated_at = now
        run.result = build_pipeline_result_from_task_runs(
            pipeline_run=run, task_runs=task_runs, status=status
        )
        saved = await self._context.pipeline_store.save(run)
        await self._context.commit()
        return saved

    # ── task transitions ─────────────────────────────────────────────────────

    async def _submit(
        self,
        run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
        task_run: PipelineTaskRunManifest,
    ) -> JobManifest:
        """Create the task's Job (QUEUED) and attach it to the task run. Commits
        nothing: the caller commits before it dispatches."""
        inputs = await self._input_resolver.resolve(
            pipeline_run=run, task_definition=task_definition, task_run=task_run
        )
        job = await self._context.job_store.create(
            self._planner.build_job_for_task(
                pipeline_run=run, task=task_run, inputs=inputs
            )
        )
        await self._events.job_queued(job)

        now = utc_now()
        await self._context.pipeline_store.save_task(
            task_run.model_copy(
                update={
                    "status": PipelineTaskRunStatus.RUNNING,
                    "job_id": job.job_id,
                    "started_at": now,
                    "updated_at": now,
                }
            )
        )
        return job

    async def _observe(
        self,
        run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
        task_run: PipelineTaskRunManifest,
        job: JobManifest | None,
    ) -> PipelineTaskRunManifest:
        """The task's Job is terminal (or missing): settle the task run."""
        if job is None:
            return await self._fail_task(
                task_run,
                ErrorInfo(
                    type="PipelineTaskJobMissing",
                    message=f"task run {task_run.pipeline_task_run_id} is running "
                    f"without an existing Job ({task_run.job_id!r})",
                ),
            )
        if job.status != JobStatus.SUCCEEDED:
            return await self._fail_task(
                task_run,
                job.error
                or ErrorInfo(
                    type="PipelineTaskJobNotSucceeded",
                    message=f"Job {job.job_id} ended {job.status.value}",
                ),
            )

        try:
            task_run = await self._result_recorder.record(
                pipeline_run=run,
                task_definition=task_definition,
                task_run=task_run,
                finished_job=job,
            )
            self._quality_gate.check_task_result(
                task_definition=task_definition, task_run=task_run
            )
        except PipelineQualityBlocked as blocked:
            return await self._settle(
                task_run,
                PipelineTaskRunStatus.BLOCKED,
                ErrorInfo(type=blocked.__class__.__name__, message=str(blocked)),
            )
        except Exception as error:
            return await self._fail_task(task_run, _error_info(error))
        return task_run

    def _is_skipped(
        self, run: PipelineRunManifest, task_definition: PipelineTaskDefinition
    ) -> bool:
        """An optional task runs only when the run names params for it."""
        if not task_definition.optional:
            return False
        return not _explicit_task_params(run, task_definition)

    async def _skip(self, task_run: PipelineTaskRunManifest) -> None:
        task_run.result = PipelineTaskResult(
            pipeline_task_id=task_run.pipeline_task_id,
            pipeline_task_run_id=task_run.pipeline_task_run_id,
            job_type=task_run.job_type,
            raw_result={
                "skipped": True,
                "reason": "optional task params not provided",
            },
        )
        await self._settle(task_run, PipelineTaskRunStatus.SKIPPED, None)

    async def _fail_task(
        self, task_run: PipelineTaskRunManifest, error: ErrorInfo
    ) -> PipelineTaskRunManifest:
        return await self._settle(task_run, PipelineTaskRunStatus.FAILED, error)

    async def _settle(
        self,
        task_run: PipelineTaskRunManifest,
        status: PipelineTaskRunStatus,
        error: ErrorInfo | None,
    ) -> PipelineTaskRunManifest:
        now = utc_now()
        task_run.status = status
        task_run.error = error
        task_run.finished_at = now
        task_run.updated_at = now
        return await self._context.pipeline_store.save_task(task_run)


def _explicit_task_params(
    run: PipelineRunManifest, task_definition: PipelineTaskDefinition
) -> dict[str, Any]:
    params = run.params or {}
    for key in (
        *task_definition.param_keys,
        task_definition.pipeline_task_id,
        task_definition.name,
        task_definition.job_type.value,
    ):
        value = params.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _error_info(error: Exception) -> ErrorInfo:
    return ErrorInfo(type=error.__class__.__name__, message=str(error))
