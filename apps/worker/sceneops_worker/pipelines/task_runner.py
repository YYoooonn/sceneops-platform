from __future__ import annotations

from typing import Any

from sceneops_core.common.ids import generate_pipeline_task_run_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.builtin import get_pipeline_definition
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineRunManifest,
    PipelineTaskDefinition,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.pipelines.errors import PipelineQualityBlocked
from sceneops_worker.pipelines.input_resolver import PipelineInputResolver
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_worker.pipelines.quality_gate import PipelineQualityGate
from sceneops_worker.pipelines.result_recorder import PipelineTaskResultRecorder
from sceneops_worker.pipelines.task_execution import (
    PipelineTaskExecution,
    PipelineTaskOutcome,
    PipelineTaskRunResult,
)


class PipelineTaskRunner:
    """Executes one pipeline task as an explicit task execution state machine.

    This runner is orchestration-runtime agnostic.
    Celery, local execution, and future Airflow adapters should all consume the
    same PipelineTaskRunResult contract.
    """

    def __init__(
        self,
        context: WorkerContext,
        *,
        planner: PipelineJobPlanner | None = None,
        quality_gate: PipelineQualityGate | None = None,
        input_resolver: PipelineInputResolver | None = None,
        result_recorder: PipelineTaskResultRecorder | None = None,
    ) -> None:
        self._context = context
        self._job_runner = JobRunner(context)
        self._planner = planner or PipelineJobPlanner()
        self._quality_gate = quality_gate or PipelineQualityGate()
        self._input_resolver = input_resolver or PipelineInputResolver(context)
        self._result_recorder = result_recorder or PipelineTaskResultRecorder(context)

    async def run(
        self,
        *,
        pipeline_run_id: str,
        task_id: str,
    ) -> PipelineTaskRunResult:
        execution = await self._prepare_execution(
            pipeline_run_id=pipeline_run_id,
            task_id=task_id,
        )

        pre_execution_result = await self._handle_pre_execution_state(execution)
        if pre_execution_result is not None:
            return pre_execution_result

        try:
            await self._validate_dependencies(execution)
            await self._resolve_inputs(execution)
            await self._start_task(execution)
            await self._create_and_attach_job(execution)
            await self._execute_job(execution)
            await self._record_task_result(execution)
            await self._apply_quality_gate(execution)

            return self._build_result(
                execution,
                PipelineTaskOutcome.SUCCEEDED,
            )

        except PipelineQualityBlocked as error:
            await self._context.rollback()
            await self._block_task(execution, error)
            return self._build_result(
                execution,
                PipelineTaskOutcome.BLOCKED,
            )

        except Exception as error:
            await self._context.rollback()
            await self._fail_task(execution, error)
            raise

    # ── preparation ──────────────────────────────────────────────────────────

    async def _prepare_execution(
        self,
        *,
        pipeline_run_id: str,
        task_id: str,
    ) -> PipelineTaskExecution:
        pipeline_run = await self._load_pipeline_run(pipeline_run_id)
        definition = get_pipeline_definition(pipeline_run.type)
        task_definition = self._resolve_task_definition(definition, task_id)
        task_run = await self._load_or_create_task_run(
            pipeline_run=pipeline_run,
            task_definition=task_definition,
        )

        return PipelineTaskExecution(
            pipeline_run=pipeline_run,
            definition=definition,
            task_definition=task_definition,
            task_run=task_run,
        )

    async def _load_pipeline_run(
        self,
        pipeline_run_id: str,
    ) -> PipelineRunManifest:
        pipeline_run = await self._context.pipeline_store.get(pipeline_run_id)
        if pipeline_run is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")
        return pipeline_run

    def _resolve_task_definition(
        self,
        definition: PipelineDefinition,
        task_id: str,
    ) -> PipelineTaskDefinition:
        for task_definition in definition.tasks:
            if task_definition.pipeline_task_id == task_id:
                return task_definition

        raise ValueError(f"Task '{task_id}' not found in pipeline '{definition.type}'")

    async def _load_or_create_task_run(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
    ) -> PipelineTaskRunManifest:
        existing = await self._context.pipeline_store.find_task(
            pipeline_run_id=pipeline_run.pipeline_run_id,
            task_id=task_definition.pipeline_task_id,
        )
        if existing is not None:
            return existing

        now = utc_now()
        return await self._context.pipeline_store.create_task(
            PipelineTaskRunManifest(
                pipeline_task_run_id=generate_pipeline_task_run_id(),
                pipeline_run_id=pipeline_run.pipeline_run_id,
                pipeline_task_id=task_definition.pipeline_task_id,
                pipeline_task_name=task_definition.name,
                task_order=task_definition.order,
                status=PipelineTaskRunStatus.PENDING,
                job_type=task_definition.job_type,
                depends_on_task_ids=task_definition.depends_on_pipeline_task_ids,
                params=task_definition.default_params,
                created_at=now,
                updated_at=now,
            )
        )

    # ── pre-execution state ──────────────────────────────────────────────────

    async def _handle_pre_execution_state(
        self,
        execution: PipelineTaskExecution,
    ) -> PipelineTaskRunResult | None:
        if execution.task_run.status == PipelineTaskRunStatus.SUCCEEDED:
            await self._try_attach_existing_job(execution)
            return self._build_result(
                execution,
                PipelineTaskOutcome.ALREADY_SUCCEEDED,
            )

        if execution.task_run.status == PipelineTaskRunStatus.SKIPPED:
            return self._build_result(
                execution,
                PipelineTaskOutcome.ALREADY_SKIPPED,
            )

        if self._should_skip_task(execution):
            await self._skip_task(execution)
            return self._build_result(
                execution,
                PipelineTaskOutcome.SKIPPED,
            )

        return None

    async def _try_attach_existing_job(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        job_id = execution.task_run.job_id
        if not job_id:
            return

        job = await self._context.job_store.get(job_id)
        if job is not None:
            execution.update_job(job)

    def _should_skip_task(
        self,
        execution: PipelineTaskExecution,
    ) -> bool:
        if not execution.task_definition.optional:
            return False

        return not self._get_explicit_task_params(execution)

    def _get_explicit_task_params(
        self,
        execution: PipelineTaskExecution,
    ) -> dict[str, Any]:
        task_definition = execution.task_definition
        pipeline_params = execution.pipeline_run.params or {}

        candidate_keys: list[str] = []

        param_keys = getattr(task_definition, "param_keys", None)
        if param_keys:
            candidate_keys.extend(param_keys)

        candidate_keys.extend(
            [
                task_definition.pipeline_task_id,
                task_definition.name,
                task_definition.job_type.value,
            ]
        )

        for key in candidate_keys:
            value = pipeline_params.get(key)
            if isinstance(value, dict) and value:
                return value

        return {}

    # ── execution flow ───────────────────────────────────────────────────────

    async def _validate_dependencies(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        acceptable_statuses = {
            PipelineTaskRunStatus.SUCCEEDED,
            PipelineTaskRunStatus.SKIPPED,
        }

        for dependency_task_id in execution.task_run.depends_on_task_ids:
            dependency = await self._context.pipeline_store.find_task(
                pipeline_run_id=execution.pipeline_run_id,
                task_id=dependency_task_id,
            )

            if dependency is None:
                raise RuntimeError(
                    f"Pipeline task dependency has not completed: "
                    f"{dependency_task_id}"
                )

            if dependency.status not in acceptable_statuses:
                raise RuntimeError(
                    f"Pipeline task dependency is not ready: "
                    f"{dependency_task_id} (status={dependency.status})"
                )

    async def _resolve_inputs(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        inputs = await self._input_resolver.resolve(
            pipeline_run=execution.pipeline_run,
            task_definition=execution.task_definition,
            task_run=execution.task_run,
        )
        execution.update_inputs(inputs)

    async def _start_task(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        now = utc_now()
        task_run = execution.task_run

        task_run.status = PipelineTaskRunStatus.RUNNING
        task_run.started_at = task_run.started_at or now
        task_run.updated_at = now
        task_run.error = None

        saved_task_run = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()

        execution.update_task_run(saved_task_run)

    async def _create_and_attach_job(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        if execution.inputs is None:
            raise RuntimeError(
                "Pipeline task inputs must be resolved before job planning."
            )

        job_manifest = self._planner.build_job_for_task(
            pipeline_run=execution.pipeline_run,
            task=execution.task_run,
            inputs=execution.inputs,
        )
        created_job = await self._context.job_store.create(job_manifest)

        task_run = execution.task_run
        task_run.job_id = created_job.job_id
        task_run.updated_at = utc_now()

        saved_task_run = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()

        execution.attach_job(
            job=created_job,
            task_run=saved_task_run,
        )

    async def _execute_job(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        if execution.job is None:
            raise RuntimeError("Job must be created before execution.")

        finished_job = await self._job_runner.run(execution.job.job_id)
        execution.update_job(finished_job)

    async def _record_task_result(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        if execution.job is None:
            raise RuntimeError("Finished job is required before result recording.")

        saved_task_run = await self._result_recorder.record(
            pipeline_run=execution.pipeline_run,
            task_definition=execution.task_definition,
            task_run=execution.task_run,
            finished_job=execution.job,
        )

        execution.update_task_run(saved_task_run)

    async def _apply_quality_gate(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        self._quality_gate.check_task_result(
            task_definition=execution.task_definition,
            task_run=execution.task_run,
        )

    # ── terminal transitions ─────────────────────────────────────────────────

    async def _skip_task(
        self,
        execution: PipelineTaskExecution,
    ) -> None:
        now = utc_now()
        task_run = execution.task_run

        task_run.status = PipelineTaskRunStatus.SKIPPED
        task_run.result = PipelineTaskResult(
            pipeline_task_id=task_run.pipeline_task_id,
            pipeline_task_run_id=task_run.pipeline_task_run_id,
            job_type=task_run.job_type,
            raw_result={
                "skipped": True,
                "reason": "optional task params not provided",
            },
        )
        task_run.finished_at = now
        task_run.updated_at = now

        saved_task_run = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()

        execution.update_task_run(saved_task_run)

    async def _block_task(
        self,
        execution: PipelineTaskExecution,
        error: PipelineQualityBlocked,
    ) -> None:
        now = utc_now()
        task_run = execution.task_run

        task_run.status = PipelineTaskRunStatus.BLOCKED
        task_run.error = ErrorInfo(
            type=error.__class__.__name__,
            message=str(error),
        )
        task_run.finished_at = now
        task_run.updated_at = now

        saved_task_run = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()

        execution.update_task_run(saved_task_run)

    async def _fail_task(
        self,
        execution: PipelineTaskExecution,
        error: Exception,
    ) -> None:
        now = utc_now()
        task_run = execution.task_run

        task_run.status = PipelineTaskRunStatus.FAILED
        task_run.error = ErrorInfo(
            type=error.__class__.__name__,
            message=str(error),
        )
        task_run.finished_at = now
        task_run.updated_at = now

        saved_task_run = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()

        execution.update_task_run(saved_task_run)

    # ── result ───────────────────────────────────────────────────────────────

    def _build_result(
        self,
        execution: PipelineTaskExecution,
        outcome: PipelineTaskOutcome,
    ) -> PipelineTaskRunResult:
        return PipelineTaskRunResult(
            pipeline_run=execution.pipeline_run,
            task_definition=execution.task_definition,
            task_run=execution.task_run,
            outcome=outcome,
            job=execution.job,
        )
