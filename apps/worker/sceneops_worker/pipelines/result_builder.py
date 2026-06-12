from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.pipelines.schemas import (
    PipelineLineage,
    PipelineRunManifest,
    PipelineRunResult,
    PipelineRunStatus,
    PipelineTaskResult,
    PipelineTaskRunManifest,
)


def build_pipeline_result_from_task_runs(
    *,
    pipeline_run: PipelineRunManifest,
    task_runs: list[PipelineTaskRunManifest],
    status: PipelineRunStatus,
) -> PipelineRunResult:
    """Build PipelineRunResult from persisted PipelineTaskRun.result records.

    Each bucket is sourced exclusively from its dedicated task result bucket:
      outputs         ← task.result.refs   (pipeline-level downstream refs)
      metrics         ← task.result.metrics
      lineage.artifacts ← task.result.artifacts
      summary         ← pipeline status + task count fields only

    Tasks are processed in task_order; later tasks win on conflicting keys.
    raw_result is never promoted — it stays inside result.tasks[].raw_result.
    """
    tasks: list[PipelineTaskResult] = []
    outputs: JsonDict = {}
    metrics: JsonDict = {}
    artifacts: dict[str, str] = {}

    counts: dict[str, int] = {
        "succeeded": 0,
        "skipped": 0,
        "blocked": 0,
        "failed": 0,
    }

    for task_run in sorted(task_runs, key=lambda t: t.task_order):
        task_status = str(task_run.status)
        if task_status in counts:
            counts[task_status] += 1

        if task_run.result is not None:
            tr = task_run.result
            tasks.append(tr)

            for k, v in tr.refs.items():
                if v is not None:
                    outputs[k] = v

            for k, v in tr.metrics.items():
                if v is not None:
                    metrics[k] = v

            for k, v in tr.artifacts.items():
                if isinstance(v, str) and v:
                    artifacts[k] = v

        elif task_run.job_id is not None:
            tasks.append(
                PipelineTaskResult(
                    pipeline_task_id=task_run.pipeline_task_id,
                    pipeline_task_run_id=task_run.pipeline_task_run_id,
                    job_type=task_run.job_type,
                    job_id=task_run.job_id,
                    job_status=task_run.status,
                )
            )

    run_summary: JsonDict = {
        "status": status.value,
        "task_count": len(task_runs),
        "succeeded_task_count": counts["succeeded"],
        "skipped_task_count": counts["skipped"],
        "blocked_task_count": counts["blocked"],
        "failed_task_count": counts["failed"],
    }

    return PipelineRunResult(
        summary=run_summary,
        lineage=PipelineLineage(artifacts=artifacts),
        outputs=outputs,
        metrics=metrics,
        tasks=tasks,
    )
