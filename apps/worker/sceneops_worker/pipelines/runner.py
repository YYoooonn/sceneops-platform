from __future__ import annotations

from sceneops_core.schemas.common import ErrorInfo
from sceneops_core.schemas.jobs import JobType, ValidateDatasetJobResult
from sceneops_core.schemas.pipelines import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepResult,
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
from sceneops_worker.pipelines.results import (
    build_pipeline_result,
    build_pipeline_step_result,
)
from sceneops_worker.pipelines.store import PipelineStore
from sceneops_worker.pipelines.errors import PipelineBlockedByValidationError


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
        step_results: list[PipelineStepResult] = []

        try:
            steps = await self.pipeline_store.list_steps(pipeline_run_id)
            steps = sorted(steps, key=lambda step: step.step_order)

            for step in steps:
                saved_step = await self._run_step(
                    pipeline_run=pipeline_run,
                    step=step,
                    context=context,
                )

                if saved_step.result is not None:
                    step_results.append(
                        PipelineStepResult.model_validate(saved_step.result)
                    )

            return await self._mark_pipeline_succeeded(
                pipeline_run,
                context=context,
                step_results=step_results,
            )

        except Exception as error:
            await self._mark_pipeline_failed(
                pipeline_run,
                context=context,
                step_results=step_results,
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
        # before run
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
            step.result = build_pipeline_step_result(step=step, job=finished_job)
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

            # after run
            self._raise_if_validation_blocked(
                job_type=step.job_type,
                result=finished_job.result,
            )

            return saved

        except PipelineBlockedByValidationError:
            raise

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

    def _raise_if_validation_blocked(
        self,
        *,
        job_type: JobType,
        result: dict | None,
    ) -> None:
        if result is None:
            return

        if job_type != JobType.VALIDATE_DATASET:
            return

        parsed = ValidateDatasetJobResult.model_validate(result)

        if parsed.should_block_pipeline:
            raise RuntimeError(
                "Dataset validation blocked pipeline: "
                f"dataset={parsed.dataset_id}:{parsed.dataset_version}, "
                f"status={parsed.status.value}, "
                f"report={parsed.validation_report_uri}"
            )

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

        return await self.pipeline_store.save_pipeline_run(pipeline_run)

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
        return await self.pipeline_store.save_pipeline_run(pipeline_run)
