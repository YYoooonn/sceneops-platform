from __future__ import annotations

from sceneops_core.schemas.common import ErrorInfo
from sceneops_core.schemas.pipelines import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_core.time import utc_now

# from sceneops_db.utils import to_error_json
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.jobs.store import JobStore
from sceneops_worker.pipelines.context import PipelineExecutionContext
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_worker.pipelines.propagation import PipelineResultPropagator
from sceneops_worker.pipelines.store import PipelineStore


class PipelineRunner:
    def __init__(
        self,
        *,
        pipeline_store: PipelineStore,
        job_store: JobStore,
        job_runner: JobRunner,
        planner: PipelineJobPlanner | None = None,
        propagator: PipelineResultPropagator | None = None,
    ) -> None:
        self.pipeline_store = pipeline_store
        self.job_store = job_store
        self.job_runner = job_runner
        self.planner = planner or PipelineJobPlanner()
        self.propagator = propagator or PipelineResultPropagator()

    async def run(self, pipeline_run_id: str) -> PipelineRunManifest:
        pipeline_run = await self.pipeline_store.get_pipeline_run(pipeline_run_id)

        if pipeline_run is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")

        self._validate_runnable(pipeline_run)

        pipeline_run = await self._mark_pipeline_running(pipeline_run)
        context = PipelineExecutionContext.from_pipeline_run(pipeline_run)

        try:
            steps = await self.pipeline_store.list_steps(pipeline_run_id)
            steps = sorted(steps, key=lambda step: step.step_order)

            for step in steps:
                await self._run_step(
                    pipeline_run=pipeline_run,
                    step=step,
                    context=context,
                )

            return await self._mark_pipeline_succeeded(
                pipeline_run,
                context=context,
            )

        except Exception as error:
            await self._mark_pipeline_failed(
                pipeline_run,
                error=ErrorInfo(
                    type=error.__class__.__name__,
                    message=str(error),
                ),
            )
            raise

    def _validate_runnable(self, pipeline_run: PipelineRunManifest) -> None:
        if pipeline_run.status == PipelineRunStatus.SUCCEEDED:
            raise RuntimeError(
                f"Pipeline run is already succeeded: {pipeline_run.pipeline_run_id}"
            )

        if pipeline_run.status == PipelineRunStatus.RUNNING:
            raise RuntimeError(
                f"Pipeline run is already running: {pipeline_run.pipeline_run_id}"
            )

        if pipeline_run.status == PipelineRunStatus.CANCELED:
            raise RuntimeError(
                f"Pipeline run is canceled: {pipeline_run.pipeline_run_id}"
            )

    async def _run_step(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> PipelineStepRunManifest:
        self._validate_dependencies_succeeded(step=step, context=context)

        step = await self._mark_step_running(step)

        try:
            job = self.planner.build_job_for_step(
                pipeline_run=pipeline_run,
                step=step,
                context=context,
            )

            created_job = await self.job_store.create_job(job)

            step.job_id = created_job.job_id
            step.updated_at = utc_now()
            step = await self.pipeline_store.save_step(step)

            finished_job = await self.job_runner.run(created_job.job_id)

            if finished_job.result is not None:
                self.propagator.apply_step_result(
                    step=step,
                    result=finished_job.result,
                    context=context,
                )

            step.status = PipelineStepRunStatus.SUCCEEDED
            step.result = {
                "job_id": finished_job.job_id,
                "job_status": (
                    finished_job.status.value
                    if hasattr(finished_job.status, "value")
                    else str(finished_job.status)
                ),
                "job_result": finished_job.result,
            }
            step.error = None
            step.finished_at = utc_now()
            step.updated_at = step.finished_at

            saved = await self.pipeline_store.save_step(step)

            context.mark_step(
                step_name=saved.step_name,
                status=(
                    saved.status.value
                    if hasattr(saved.status, "value")
                    else str(saved.status)
                ),
                job_id=saved.job_id,
                result=saved.result,
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

            await self.pipeline_store.save_step(step)
            raise

    def _validate_dependencies_succeeded(
        self,
        *,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> None:
        for dependency in step.depends_on_step_names:
            dependency_state = context.steps.get(dependency)

            if dependency_state is None:
                raise RuntimeError(
                    f"Step dependency has not completed: "
                    f"{step.step_name} depends on {dependency}"
                )

            if dependency_state.get("status") != PipelineStepRunStatus.SUCCEEDED.value:
                raise RuntimeError(
                    f"Step dependency is not succeeded: "
                    f"{step.step_name} depends on {dependency}"
                )

    async def _mark_step_running(
        self,
        step: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest:
        now = utc_now()
        step.status = PipelineStepRunStatus.RUNNING
        step.started_at = step.started_at or now
        step.updated_at = now
        step.error = None

        return await self.pipeline_store.save_step(step)

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

        return await self.pipeline_store.save_pipeline_run(pipeline_run)

    async def _mark_pipeline_succeeded(
        self,
        pipeline_run: PipelineRunManifest,
        *,
        context: PipelineExecutionContext,
    ) -> PipelineRunManifest:
        now = utc_now()
        steps = await self.pipeline_store.list_steps(pipeline_run.pipeline_run_id)

        pipeline_run.status = PipelineRunStatus.SUCCEEDED
        pipeline_run.result = {
            "dataset_manifest_uri": context.get("dataset_manifest_uri"),
            "inference_run_id": context.get("inference_run_id"),
            "prediction_manifest_uri": context.get("prediction_manifest_uri"),
            "evaluation_run_id": context.get("evaluation_run_id"),
            "evaluation_manifest_uri": context.get("evaluation_manifest_uri"),
            "metrics": context.get("metrics"),
            "steps": [
                {
                    "step_name": step.step_name,
                    "status": (
                        step.status.value
                        if hasattr(step.status, "value")
                        else str(step.status)
                    ),
                    "job_id": step.job_id,
                    "result": step.result,
                }
                for step in sorted(steps, key=lambda item: item.step_order)
            ],
        }
        pipeline_run.error = None
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now

        return await self.pipeline_store.save_pipeline_run(pipeline_run)

    async def _mark_pipeline_failed(
        self,
        pipeline_run: PipelineRunManifest,
        *,
        error: ErrorInfo,
    ) -> PipelineRunManifest:
        now = utc_now()
        pipeline_run.status = PipelineRunStatus.FAILED
        pipeline_run.error = error
        pipeline_run.finished_at = now
        pipeline_run.updated_at = now

        return await self.pipeline_store.save_pipeline_run(pipeline_run)
