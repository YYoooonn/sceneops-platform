from __future__ import annotations

from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_core.pipelines.schemas import (
    PipelineLineage,
    PipelineRunManifest,
    PipelineRunResult,
    PipelineRunStatus,
    PipelineStepResult,
    PipelineStepRunManifest,
)
from sceneops_worker.pipelines.context import PipelineExecutionContext


def build_pipeline_result(
    *,
    pipeline_run: PipelineRunManifest,
    context: PipelineExecutionContext,
    steps: list[PipelineStepResult],
    status: PipelineRunStatus,
) -> PipelineRunResult:
    summary: dict = {
        "status": status.value,
        "dataset_id": pipeline_run.dataset_id,
        "dataset_version": pipeline_run.dataset_version,
        "model_id": pipeline_run.model_id,
        "model_version": pipeline_run.model_version,
        **{k: v for k, v in context.values.items() if v is not None},
    }

    return PipelineRunResult(
        summary=summary,
        lineage=PipelineLineage(
            artifacts={
                k: v
                for k, v in context.values.items()
                if isinstance(v, str) and k.endswith("_uri")
            }
        ),
        outputs={},
        steps=steps,
    )


def build_pipeline_step_result(
    *,
    step: PipelineStepRunManifest,
    job: JobManifest | None,
) -> PipelineStepResult:
    job_result = job.result if job is not None else None
    job_id = job.job_id if job is not None else step.job_id

    return PipelineStepResult(
        pipeline_step_id=step.pipeline_step_id,
        pipeline_step_name=step.pipeline_step_name,
        job_type=step.job_type,
        job_id=job_id,
        status=JobStatus.SUCCEEDED if job_result is not None else JobStatus.FAILED,
        result=job_result,
        error=step.error,
    )
