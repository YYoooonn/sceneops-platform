from __future__ import annotations

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.builtin import get_pipeline_definition
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.pipelines.result_builder import (
    build_pipeline_result_from_task_runs,
)
from sceneops_worker.pipelines.task_execution import (
    PipelineTaskOutcome,
    PipelineTaskRunResult,
)
from sceneops_worker.pipelines.task_runner import PipelineTaskRunner


class PipelineRunner:
    """Local/dev sequential orchestrator for a full pipeline run."""

    def __init__(self, context: WorkerContext) -> None:
        self._context = context
        self._task_runner = PipelineTaskRunner(context)

    async def run(self, pipeline_run_id: str) -> PipelineRunManifest:
        pipeline_run = await self._load_pipeline_run(pipeline_run_id)

        self._validate_runnable(pipeline_run)

        pipeline_run = await self._start_pipeline(pipeline_run)

        definition = get_pipeline_definition(pipeline_run.type)
        ordered_tasks = sorted(definition.tasks, key=lambda task: task.order)

        try:
            for task_def in ordered_tasks:
                task_result = await self._task_runner.run(
                    pipeline_run_id=pipeline_run.pipeline_run_id,
                    task_id=task_def.pipeline_task_id,
                )

                if self._is_blocked_task(task_result):
                    return await self._block_pipeline_from_task_result(
                        pipeline_run=pipeline_run,
                        task_result=task_result,
                    )

            finished_task_runs = await self._list_task_runs(
                pipeline_run.pipeline_run_id
            )

            return await self._succeed_pipeline(
                pipeline_run=pipeline_run,
                task_runs=finished_task_runs,
            )

        except Exception as error:
            completed_task_runs = await self._list_task_runs(
                pipeline_run.pipeline_run_id
            )

            return await self._fail_and_raise(
                pipeline_run=pipeline_run,
                task_runs=completed_task_runs,
                error=error,
            )

    # ── per-task DAG bridge (Airflow) ────────────────────────────────────────
    async def start(self, pipeline_run_id: str) -> PipelineRunManifest:
        pipeline_run = await self._load_pipeline_run(pipeline_run_id)
        self._validate_runnable(pipeline_run)
        return await self._start_pipeline(pipeline_run)

    async def finalize(self, pipeline_run_id: str) -> PipelineRunManifest:
        pipeline_run = await self._load_pipeline_run(pipeline_run_id)
        task_runs = await self._list_task_runs(pipeline_run_id)

        if pipeline_run.status != PipelineRunStatus.RUNNING:
            # start() never completed (e.g. validation rejected it before any
            # task ran) — there is nothing to roll up.
            return await self._fail_pipeline(
                pipeline_run=pipeline_run,
                task_runs=task_runs,
                error=ErrorInfo(
                    type="PipelineNeverStarted",
                    message=(
                        f"finalize() called but pipeline_run status is "
                        f"{pipeline_run.status.value!r}, not 'running' — "
                        "start() must have failed or never ran."
                    ),
                ),
            )

        if any(t.status == PipelineTaskRunStatus.BLOCKED for t in task_runs):
            return await self._block_pipeline(
                pipeline_run=pipeline_run,
                task_runs=task_runs,
                error=ErrorInfo(
                    type="PipelineQualityBlocked",
                    message="One or more tasks were blocked by a quality gate.",
                ),
            )

        if any(t.status == PipelineTaskRunStatus.FAILED for t in task_runs):
            return await self._fail_pipeline(
                pipeline_run=pipeline_run,
                task_runs=task_runs,
                error=ErrorInfo(
                    type="PipelineTaskFailed",
                    message="One or more tasks failed.",
                ),
            )

        return await self._succeed_pipeline(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
        )

    # ── loading / validation ─────────────────────────────────────────────────

    async def _load_pipeline_run(
        self,
        pipeline_run_id: str,
    ) -> PipelineRunManifest:
        pipeline_run = await self._context.pipeline_store.get(pipeline_run_id)

        if pipeline_run is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")

        return pipeline_run

    def _validate_runnable(self, pipeline_run: PipelineRunManifest) -> None:
        if pipeline_run.status == PipelineRunStatus.SUCCEEDED:
            raise RuntimeError(
                f"Pipeline run is already succeeded: {pipeline_run.pipeline_run_id}"
            )

        if pipeline_run.status == PipelineRunStatus.RUNNING:
            raise RuntimeError(
                f"Pipeline run is already running: {pipeline_run.pipeline_run_id}"
            )

        if pipeline_run.status == PipelineRunStatus.CANCELLED:
            raise RuntimeError(
                f"Pipeline run is cancelled: {pipeline_run.pipeline_run_id}"
            )

        # BLOCKED is intentionally retryable: it means a quality gate stopped
        # the pipeline (e.g. validate_scene), not that the pipeline failed to
        # run. Once the underlying issue is fixed, redispatching should
        # re-evaluate the blocking task. The API layer's
        # PipelineService.validate_executable already allows this; this check
        # used to be inconsistent with it.

    # ── execution ────────────────────────────────────────────────────────────

    async def _start_pipeline(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.RUNNING
        pipeline_run.started_at = pipeline_run.started_at or now
        pipeline_run.updated_at = now
        pipeline_run.finished_at = None
        pipeline_run.error = None

        saved = await self._context.pipeline_store.save(pipeline_run)
        await self._context.commit()

        return saved

    def _is_blocked_task(
        self,
        task_result: PipelineTaskRunResult,
    ) -> bool:
        return task_result.outcome == PipelineTaskOutcome.BLOCKED

    async def _list_task_runs(
        self,
        pipeline_run_id: str,
    ) -> list[PipelineTaskRunManifest]:
        return await self._context.pipeline_store.list_tasks(pipeline_run_id)

    # ── terminal transitions ─────────────────────────────────────────────────

    async def _succeed_pipeline(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_runs: list[PipelineTaskRunManifest],
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.SUCCEEDED
        pipeline_run.error = None
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now
        pipeline_run.result = build_pipeline_result_from_task_runs(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
            status=PipelineRunStatus.SUCCEEDED,
        )

        saved = await self._context.pipeline_store.save(pipeline_run)
        await self._context.commit()

        return saved

    async def _block_pipeline_from_task_result(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_result: PipelineTaskRunResult,
    ) -> PipelineRunManifest:
        task_runs = await self._list_task_runs(pipeline_run.pipeline_run_id)

        message = self._build_blocked_message(task_result)

        return await self._block_pipeline(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
            error=ErrorInfo(
                type="PipelineQualityBlocked",
                message=message,
            ),
        )

    async def _block_pipeline(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_runs: list[PipelineTaskRunManifest],
        error: ErrorInfo,
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.BLOCKED
        pipeline_run.error = error
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now
        pipeline_run.result = build_pipeline_result_from_task_runs(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
            status=PipelineRunStatus.BLOCKED,
        )

        saved = await self._context.pipeline_store.save(pipeline_run)
        await self._context.commit()

        return saved

    async def _fail_pipeline(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_runs: list[PipelineTaskRunManifest],
        error: ErrorInfo,
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.FAILED
        pipeline_run.error = error
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now
        pipeline_run.result = build_pipeline_result_from_task_runs(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
            status=PipelineRunStatus.FAILED,
        )

        saved = await self._context.pipeline_store.save(pipeline_run)
        await self._context.commit()

        return saved

    async def _fail_and_raise(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_runs: list[PipelineTaskRunManifest],
        error: Exception,
    ) -> PipelineRunManifest:
        await self._context.rollback()

        await self._fail_pipeline(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
            error=ErrorInfo(
                type=error.__class__.__name__,
                message=str(error),
            ),
        )

        raise error

    # ── messages ─────────────────────────────────────────────────────────────

    def _build_blocked_message(
        self,
        task_result: PipelineTaskRunResult,
    ) -> str:
        task_run = task_result.task_run

        if task_run.error is not None:
            return task_run.error.message

        return (
            f"Pipeline blocked by quality gate at task '{task_run.pipeline_task_id}'."
        )
