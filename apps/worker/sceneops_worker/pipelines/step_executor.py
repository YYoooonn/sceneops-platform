from __future__ import annotations

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineStepResult,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.pipelines.context import PipelineExecutionContext
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_worker.pipelines.propagation import PipelineResultPropagator
from sceneops_worker.pipelines.quality_gate import PipelineQualityGate
from sceneops_worker.pipelines.result_builder import build_pipeline_step_result


class PipelineStepExecutor:
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

    async def run_step(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> PipelineStepRunManifest:
        self._validate_dependencies_succeeded(step=step, context=context)

        step = await self._mark_step_running(step)

        try:
            job = self._planner.build_job_for_step(
                pipeline_run=pipeline_run,
                step=step,
                context=context,
            )
            created_job = await self._context.job_store.create(job)

            step.job_id = created_job.job_id
            step.updated_at = utc_now()
            step = await self._context.pipeline_store.save_step(step)

            finished_job = await self._job_runner.run(created_job.job_id)

            if finished_job.result is not None:
                self._propagator.apply_step_result(
                    step=step,
                    result=finished_job.result,
                    context=context,
                )

            step.status = PipelineStepRunStatus.SUCCEEDED
            step.result = build_pipeline_step_result(
                step=step,
                job=finished_job,
            )
            step.error = None
            step.finished_at = utc_now()
            step.updated_at = step.finished_at

            saved = await self._context.pipeline_store.save_step(step)

            context.mark_step(
                step_id=saved.step_id,
                step_name=saved.step_name,
                status=saved.status,
                job_id=saved.job_id,
                result=(
                    PipelineStepResult.model_validate(saved.result)
                    if saved.result is not None
                    else None
                ),
            )

            self._quality_gate.check_step_result(
                job_type=step.job_type,
                result=finished_job.result,
            )

            return saved

        except Exception as error:
            step.status = PipelineStepRunStatus.FAILED
            step.error = ErrorInfo(
                type=error.__class__.__name__,
                message=str(error),
            )
            step.finished_at = utc_now()
            step.updated_at = step.finished_at
            await self._context.pipeline_store.save_step(step)
            raise

    def _validate_dependencies_succeeded(
        self,
        *,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> None:
        for dep_step_id in step.depends_on_step_ids:
            context.require_step_succeeded(dep_step_id)

    async def _mark_step_running(
        self,
        step: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest:
        now = utc_now()

        step.status = PipelineStepRunStatus.RUNNING
        step.started_at = step.started_at or now
        step.updated_at = now
        step.error = None

        return await self._context.pipeline_store.save_step(step)
