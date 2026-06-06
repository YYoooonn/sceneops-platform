from __future__ import annotations

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.pipelines.context import PipelineExecutionContext
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_worker.pipelines.propagation import PipelineResultPropagator
from sceneops_worker.pipelines.quality_gate import PipelineQualityGate
from sceneops_worker.pipelines.result_builder import build_pipeline_task_result


class PipelineTaskExecutor:
    def __init__(
        self,
        context: WorkerContext,
        *,
        planner: PipelineJobPlanner | None = None,
        propagator: PipelineResultPropagator | None = None,
        quality_gate: PipelineQualityGate | None = None,
    ) -> None:
        self._context = context
        self._job_runner = JobRunner(context)
        self._planner = planner or PipelineJobPlanner()
        self._propagator = propagator or PipelineResultPropagator()
        self._quality_gate = quality_gate or PipelineQualityGate()

    async def run_task(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task: PipelineTaskRunManifest,
        context: PipelineExecutionContext,
    ) -> PipelineTaskRunManifest:
        self._validate_dependencies_succeeded(task=task, context=context)

        task = await self._mark_task_running(task)
        await self._context.commit()

        try:
            job = self._planner.build_job_for_task(
                pipeline_run=pipeline_run,
                task=task,
                context=context,
            )
            created_job = await self._context.job_store.create(job)

            task.job_id = created_job.job_id
            task.updated_at = utc_now()
            task = await self._context.pipeline_store.save_task(task)
            await self._context.commit()

            finished_job = await self._job_runner.run(created_job.job_id)

            if finished_job.result is not None:
                self._propagator.apply_task_result(
                    task=task,
                    result=finished_job.result,
                    context=context,
                )

            task.status = PipelineTaskRunStatus.SUCCEEDED
            task.result = build_pipeline_task_result(
                task=task,
                job=finished_job,
            )
            task.error = None
            task.finished_at = utc_now()
            task.updated_at = task.finished_at

            saved = await self._context.pipeline_store.save_task(task)
            await self._context.commit()

            context.mark_task(
                pipeline_task_id=saved.pipeline_task_id,
                pipeline_task_name=saved.pipeline_task_name,
                status=saved.status,
                job_id=saved.job_id,
                result=(
                    PipelineTaskResult.model_validate(saved.result)
                    if saved.result is not None
                    else None
                ),
            )

            self._quality_gate.check_task_result(
                job_type=task.job_type,
                result=finished_job.result,
            )

            return saved

        except Exception as error:
            await self._context.rollback()

            task.status = PipelineTaskRunStatus.FAILED
            task.error = ErrorInfo(
                type=error.__class__.__name__,
                message=str(error),
            )
            task.finished_at = utc_now()
            task.updated_at = task.finished_at
            await self._context.pipeline_store.save_task(task)
            await self._context.commit()

            raise

    def _validate_dependencies_succeeded(
        self,
        *,
        task: PipelineTaskRunManifest,
        context: PipelineExecutionContext,
    ) -> None:
        for dep_task_id in task.depends_on_task_ids:
            context.require_task_succeeded(dep_task_id)

    async def _mark_task_running(
        self,
        task: PipelineTaskRunManifest,
    ) -> PipelineTaskRunManifest:
        now = utc_now()

        task.status = PipelineTaskRunStatus.RUNNING
        task.started_at = task.started_at or now
        task.updated_at = now
        task.error = None

        return await self._context.pipeline_store.save_task(task)
