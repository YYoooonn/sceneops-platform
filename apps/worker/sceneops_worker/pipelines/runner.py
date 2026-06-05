from __future__ import annotations

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepResult,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.pipelines.context import PipelineExecutionContext
from sceneops_worker.pipelines.result_builder import build_pipeline_result
from sceneops_worker.pipelines.step_executor import PipelineStepExecutor


class PipelineRunner:
    def __init__(self, context: WorkerContext) -> None:
        self._context = context
        self._step_executor = PipelineStepExecutor(context)

    async def run(self, pipeline_run_id: str) -> PipelineRunManifest:
        pipeline_run = await self._load_pipeline_run(pipeline_run_id)

        self._validate_runnable(pipeline_run)

        pipeline_run = await self._mark_pipeline_running(pipeline_run)
        await self._context.commit()

        context = PipelineExecutionContext.from_pipeline_run(pipeline_run)
        step_results: list[PipelineStepResult] = []

        try:
            steps = await self._context.pipeline_store.list_steps(pipeline_run_id)
            steps = sorted(steps, key=lambda s: s.step_order)

            for step in steps:
                saved_step = await self._step_executor.run_step(
                    pipeline_run=pipeline_run,
                    step=step,
                    context=context,
                )

                if saved_step.result is not None:
                    step_results.append(
                        PipelineStepResult.model_validate(saved_step.result)
                    )

            result = await self._mark_pipeline_succeeded(
                pipeline_run,
                context=context,
                step_results=step_results,
            )
            await self._context.commit()

            return result

        except Exception as error:
            return await self._fail_and_raise(
                pipeline_run=pipeline_run,
                context=context,
                step_results=step_results,
                error=error,
            )

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

    async def _mark_pipeline_running(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.RUNNING
        pipeline_run.started_at = pipeline_run.started_at or now
        pipeline_run.updated_at = now
        pipeline_run.finished_at = None
        pipeline_run.error = None

        return await self._context.pipeline_store.save(pipeline_run)

    async def _mark_pipeline_succeeded(
        self,
        pipeline_run: PipelineRunManifest,
        *,
        context: PipelineExecutionContext,
        step_results: list[PipelineStepResult],
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.SUCCEEDED
        pipeline_run.error = None
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now
        pipeline_run.result = build_pipeline_result(
            pipeline_run=pipeline_run,
            context=context,
            steps=step_results,
            status=PipelineRunStatus.SUCCEEDED,
        )

        return await self._context.pipeline_store.save(pipeline_run)

    async def _mark_pipeline_failed(
        self,
        pipeline_run: PipelineRunManifest,
        *,
        context: PipelineExecutionContext,
        step_results: list[PipelineStepResult],
        error: ErrorInfo,
    ) -> PipelineRunManifest:
        now = utc_now()

        pipeline_run.status = PipelineRunStatus.FAILED
        pipeline_run.error = error
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now
        pipeline_run.result = build_pipeline_result(
            pipeline_run=pipeline_run,
            context=context,
            steps=step_results,
            status=PipelineRunStatus.FAILED,
        )

        return await self._context.pipeline_store.save(pipeline_run)

    async def _fail_and_raise(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        context: PipelineExecutionContext,
        step_results: list[PipelineStepResult],
        error: Exception,
    ) -> PipelineRunManifest:
        await self._context.rollback()

        await self._mark_pipeline_failed(
            pipeline_run,
            context=context,
            step_results=step_results,
            error=ErrorInfo(
                type=error.__class__.__name__,
                message=str(error),
            ),
        )
        await self._context.commit()

        raise error
