from __future__ import annotations

from typing import Any

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

    Aggregation priority per task: raw_result < summary < refs (refs highest).
    Tasks processed in task_order — later tasks win on conflicting keys.
    Artifact URIs collected from all _uri-suffixed string values in refs.
    """
    tasks: list[PipelineTaskResult] = []
    task_outputs: dict[str, Any] = {}
    artifact_uris: dict[str, str] = {}

    for task_run in sorted(task_runs, key=lambda t: t.task_order):
        if task_run.result is not None:
            tr = task_run.result
            tasks.append(tr)

            # Merge: raw_result < summary < refs
            merged: dict[str, Any] = {}
            for src in (tr.raw_result, tr.summary, tr.refs):
                for k, v in src.items():
                    if v is not None:
                        merged[k] = v

            for k, v in merged.items():
                task_outputs[k] = v
                if isinstance(v, str) and k.endswith("_uri") and v:
                    artifact_uris[k] = v

        elif task_run.job_id is not None:
            # Minimal placeholder for completed task runs with no stored result
            tasks.append(
                PipelineTaskResult(
                    pipeline_task_id=task_run.pipeline_task_id,
                    pipeline_task_run_id=task_run.pipeline_task_run_id,
                    job_type=task_run.job_type,
                    job_id=task_run.job_id,
                    job_status=task_run.status,
                )
            )

    run_summary: dict[str, Any] = {
        "status": status.value,
        "dataset_id": pipeline_run.dataset_id,
        "dataset_version": pipeline_run.dataset_version,
        "model_id": pipeline_run.model_id,
        "model_version": pipeline_run.model_version,
        **{k: v for k, v in task_outputs.items() if v is not None},
    }

    return PipelineRunResult(
        summary=run_summary,
        lineage=PipelineLineage(artifacts=artifact_uris),
        outputs={},
        tasks=tasks,
    )
