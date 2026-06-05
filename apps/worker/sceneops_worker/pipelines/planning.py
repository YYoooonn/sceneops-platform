from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.ids import generate_job_id
from sceneops_core.jobs.schemas import JobManifest, JobStatus, create_initial_job_steps
from sceneops_core.pipelines.schemas import PipelineRunManifest, PipelineStepRunManifest
from sceneops_core.common.time import utc_now
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)
from sceneops_worker.pipelines.context import PipelineExecutionContext


class PipelineJobPlanner:
    """Builds a JobManifest for a pipeline step.

    Step-specific param assembly is delegated to each handler via
    ``build_step_params(base, context_values)``. Adding a new JobType only
    requires implementing that method on the new handler — this class never
    needs to change.
    """

    def __init__(
        self,
        handler_registry: JobHandlerRegistry | None = None,
    ) -> None:
        self._registry = handler_registry or create_default_job_handler_registry()

    def build_job_for_step(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> JobManifest:
        now = utc_now()
        params = self._build_step_params(
            pipeline_run=pipeline_run,
            step=step,
            context=context,
        )

        return JobManifest(
            job_id=generate_job_id(),
            type=step.job_type,
            status=JobStatus.PENDING,
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            params=params,
            steps=create_initial_job_steps(step.job_type),
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_step_run_id=step.pipeline_step_run_id,
            pipeline_step_id=step.step_id,
            retry_count=0,
            max_retries=0,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )

    def _build_step_params(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> JsonDict:
        base: JsonDict = {
            "dataset_id": pipeline_run.dataset_id,
            "dataset_version": pipeline_run.dataset_version,
            **(step.params or {}),
        }
        handler = self._registry.get(step.job_type)
        return handler.build_step_params(base=base, context_values=context.values)
