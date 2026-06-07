from __future__ import annotations

from sceneops_core.common.ids import generate_job_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus, create_initial_job_steps
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineTaskInputs,
    PipelineTaskRunManifest,
)
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)


class PipelineJobPlanner:
    """Builds a JobManifest for a pipeline task.

    Task-specific param assembly is delegated to each handler via
    ``build_step_params(base, context_values)``. Adding a new JobType only
    requires implementing that method on the new handler — this class never
    needs to change.
    """

    def __init__(
        self,
        handler_registry: JobHandlerRegistry | None = None,
    ) -> None:
        self._registry = handler_registry or create_default_job_handler_registry()

    def build_job_for_task(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task: PipelineTaskRunManifest,
        inputs: PipelineTaskInputs,
    ) -> JobManifest:
        now = utc_now()
        params = self._build_task_params(
            pipeline_run=pipeline_run,
            task=task,
            inputs=inputs,
        )

        return JobManifest(
            job_id=generate_job_id(),
            type=task.job_type,
            status=JobStatus.PENDING,
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            params=params,
            steps=create_initial_job_steps(task.job_type),
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_task_run_id=task.pipeline_task_run_id,
            pipeline_task_id=task.pipeline_task_id,
            retry_count=0,
            max_retries=0,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )

    def _build_task_params(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task: PipelineTaskRunManifest,
        inputs: PipelineTaskInputs,
    ) -> JsonDict:
        base: JsonDict = {
            "dataset_id": pipeline_run.dataset_id,
            "dataset_version": pipeline_run.dataset_version,
            **(task.params or {}),
        }

        handler = self._registry.get(task.job_type)
        # Handlers receive a flat context_values dict for build_step_params compat,
        # but the pipeline layer itself is schema-based (PipelineTaskInputs).
        return handler.build_step_params(
            base=base, context_values=inputs.to_context_values()
        )
