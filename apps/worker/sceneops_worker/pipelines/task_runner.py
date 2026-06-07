"""PipelineTaskRunner — owns the full single-task execution use case.

In Airflow terms, this corresponds to one Airflow Task invocation.

Architecture:
    PipelineTaskRunner
    → PipelineInputResolver       resolve stable identifiers + DB-backed refs → PipelineTaskInputs
    → PipelineJobPlanner          build JobManifest from task + PipelineTaskInputs
    → JobRunner                   execute the concrete job
    → PipelineTaskResultRecorder  persist RUNNING → SUCCEEDED + normalized PipelineTaskResult
    → PipelineQualityGate         validate result against task contract

Dependency validation reads task status directly from DB — no PipelineExecutionContext required.
"""

from __future__ import annotations

from sceneops_core.common.ids import generate_pipeline_task_run_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest
from sceneops_core.pipelines.builtin import get_pipeline_definition
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineRunManifest,
    PipelineTaskDefinition,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.pipelines.input_resolver import PipelineInputResolver
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_worker.pipelines.quality_gate import PipelineQualityGate
from sceneops_worker.pipelines.result_recorder import PipelineTaskResultRecorder


class PipelineTaskRunner:
    """Executes one pipeline task given pipeline_run_id and task_id.

    This is the stable entry point for standalone task execution.
    In Airflow terms, one invocation of this runner corresponds to one Airflow
    Task; the internal task_id maps directly to the Airflow task_id.

    PipelineTaskRunner owns the full execution use case.
    Local Celery PipelineRunner delegates all per-task work here.
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

    async def run(self, *, pipeline_run_id: str, task_id: str) -> JobManifest:
        """Execute the named task for the given pipeline run and return its job."""
        pipeline_run = await self._load_pipeline_run(pipeline_run_id)
        definition = get_pipeline_definition(pipeline_run.type)
        task_def = self._resolve_task_definition(definition, task_id)
        task_run = await self._load_or_create_task_run(pipeline_run, task_def)

        # Idempotency guard — do not re-execute an already-succeeded task.
        # TODO: revisit before enabling Airflow retry semantics.
        if task_run.status == PipelineTaskRunStatus.SUCCEEDED and task_run.job_id:
            job = await self._context.job_store.get(task_run.job_id)
            if job is not None:
                return job

        # Validate upstream dependencies directly from DB.
        await self._validate_dependencies(task_run, pipeline_run_id)

        # Resolve all task inputs from DB records — dataset/model registry,
        # upstream task results, and stable pipeline/task identifiers.
        inputs = await self._input_resolver.resolve(
            pipeline_run=pipeline_run,
            task_definition=task_def,
            task_run=task_run,
        )

        # Mark task RUNNING and commit — from this point any exception triggers
        # the FAILED transition below.
        task_run = await self._mark_task_running(task_run)
        await self._context.commit()

        try:
            job_manifest = self._planner.build_job_for_task(
                pipeline_run=pipeline_run,
                task=task_run,
                inputs=inputs,
            )
            created_job = await self._context.job_store.create(job_manifest)

            task_run.job_id = created_job.job_id
            task_run.updated_at = utc_now()
            task_run = await self._context.pipeline_store.save_task(task_run)
            await self._context.commit()

            # Execute the job.
            finished_job = await self._job_runner.run(created_job.job_id)

            # Persist RUNNING → SUCCEEDED and store normalized PipelineTaskResult.
            await self._result_recorder.record(
                pipeline_run=pipeline_run,
                task_definition=task_def,
                task_run=task_run,
                finished_job=finished_job,
            )

            # Validate result against task contract (may raise — caught below).
            self._quality_gate.check_task_result(
                job_type=task_run.job_type,
                result=finished_job.result,
            )

            return finished_job

        except Exception as error:
            await self._context.rollback()
            await self._mark_task_failed(task_run, error)
            raise

    # ── private helpers ───────────────────────────────────────────────────────

    async def _load_pipeline_run(self, pipeline_run_id: str) -> PipelineRunManifest:
        run = await self._context.pipeline_store.get(pipeline_run_id)
        if run is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")
        return run

    def _resolve_task_definition(
        self,
        definition: PipelineDefinition,
        task_id: str,
    ) -> PipelineTaskDefinition:
        for task_def in definition.tasks:
            if task_def.pipeline_task_id == task_id:
                return task_def
        raise ValueError(f"Task '{task_id}' not found in pipeline '{definition.type}'")

    async def _load_or_create_task_run(
        self,
        pipeline_run: PipelineRunManifest,
        task_def: PipelineTaskDefinition,
    ) -> PipelineTaskRunManifest:
        existing = await self._context.pipeline_store.find_task(
            pipeline_run_id=pipeline_run.pipeline_run_id,
            task_id=task_def.pipeline_task_id,
        )
        if existing is not None:
            return existing

        now = utc_now()
        return await self._context.pipeline_store.create_task(
            PipelineTaskRunManifest(
                pipeline_task_run_id=generate_pipeline_task_run_id(),
                pipeline_run_id=pipeline_run.pipeline_run_id,
                pipeline_task_id=task_def.pipeline_task_id,
                pipeline_task_name=task_def.name,
                task_order=task_def.order,
                status=PipelineTaskRunStatus.PENDING,
                job_type=task_def.job_type,
                depends_on_task_ids=task_def.depends_on_pipeline_task_ids,
                params=task_def.default_params,
                created_at=now,
                updated_at=now,
            )
        )

    async def _validate_dependencies(
        self,
        task_run: PipelineTaskRunManifest,
        pipeline_run_id: str,
    ) -> None:
        for dep_task_id in task_run.depends_on_task_ids:
            dep_run = await self._context.pipeline_store.find_task(
                pipeline_run_id=pipeline_run_id,
                task_id=dep_task_id,
            )
            if dep_run is None:
                raise RuntimeError(
                    f"Pipeline task dependency has not completed: {dep_task_id}"
                )
            if dep_run.status != PipelineTaskRunStatus.SUCCEEDED:
                raise RuntimeError(
                    f"Pipeline task dependency is not succeeded: "
                    f"{dep_task_id} (status={dep_run.status})"
                )

    async def _mark_task_running(
        self,
        task_run: PipelineTaskRunManifest,
    ) -> PipelineTaskRunManifest:
        now = utc_now()
        task_run.status = PipelineTaskRunStatus.RUNNING
        task_run.started_at = task_run.started_at or now
        task_run.updated_at = now
        task_run.error = None
        return await self._context.pipeline_store.save_task(task_run)

    async def _mark_task_failed(
        self,
        task_run: PipelineTaskRunManifest,
        error: Exception,
    ) -> None:
        now = utc_now()
        task_run.status = PipelineTaskRunStatus.FAILED
        task_run.error = ErrorInfo(
            type=error.__class__.__name__,
            message=str(error),
        )
        task_run.finished_at = now
        task_run.updated_at = now
        await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()
