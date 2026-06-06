from __future__ import annotations

from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_core.pipelines.schemas import (
    PipelineLineage,
    PipelineRunManifest,
    PipelineRunResult,
    PipelineRunStatus,
    PipelineTaskResult,
    PipelineTaskRunManifest,
)
from sceneops_worker.pipelines.context import PipelineExecutionContext


def build_pipeline_result(
    *,
    pipeline_run: PipelineRunManifest,
    context: PipelineExecutionContext,
    tasks: list[PipelineTaskResult],
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
        tasks=tasks,
    )


def build_pipeline_task_result(
    *,
    task: PipelineTaskRunManifest,
    job: JobManifest | None,
) -> PipelineTaskResult:
    job_result = job.result if job is not None else None
    job_id = job.job_id if job is not None else task.job_id

    return PipelineTaskResult(
        pipeline_task_id=task.pipeline_task_id,
        pipeline_task_name=task.pipeline_task_name,
        job_type=task.job_type,
        job_id=job_id,
        status=JobStatus.SUCCEEDED if job_result is not None else JobStatus.FAILED,
        result=job_result,
        error=task.error,
    )
