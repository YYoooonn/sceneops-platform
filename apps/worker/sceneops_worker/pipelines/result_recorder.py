from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineTaskDefinition,
    PipelineTaskOutputKind,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext


def _read_dot_path(data: dict, path: str) -> Any:
    """Read a value from a nested dict using a dot-separated path."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


@dataclass
class NormalizedTaskResult:
    refs: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    raw_result: dict = field(default_factory=dict)


def normalize_task_outputs(
    raw_result: JsonDict,
    task_definition: PipelineTaskDefinition,
) -> NormalizedTaskResult:
    """Normalize raw job result into structured buckets using task output specs."""
    normalized = NormalizedTaskResult(raw_result=dict(raw_result))

    for output in task_definition.outputs:
        value = _read_dot_path(raw_result, output.source)

        if value is None:
            if output.default is not None:
                value = output.default
            elif output.required:
                raise ValueError(
                    f"Required output '{output.name}' (source='{output.source}') "
                    f"is missing from job result for task "
                    f"'{task_definition.pipeline_task_id}'."
                )
            else:
                continue

        target_key = output.target or output.name

        if output.kind == PipelineTaskOutputKind.REF:
            normalized.refs[target_key] = value
        elif output.kind == PipelineTaskOutputKind.SUMMARY:
            normalized.summary[target_key] = value
        elif output.kind == PipelineTaskOutputKind.METRIC:
            normalized.metrics[target_key] = value
        elif output.kind == PipelineTaskOutputKind.ARTIFACT:
            normalized.artifacts[target_key] = value

    return normalized


class PipelineTaskResultRecorder:
    """Persists a finished pipeline task run result to the DB.

    Called by PipelineTaskRunner after JobRunner completes.
    Uses task_definition.outputs to normalize the raw job result into
    pipeline-level refs/summary/metrics/artifacts buckets.
    """

    def __init__(self, context: WorkerContext) -> None:
        self._context = context

    async def record(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
        task_run: PipelineTaskRunManifest,
        finished_job: JobManifest,
    ) -> PipelineTaskRunManifest:
        """Persist the finished job result into the task run record."""
        self._validate_recording_contract(
            pipeline_run=pipeline_run,
            task_definition=task_definition,
            task_run=task_run,
            finished_job=finished_job,
        )

        now = utc_now()
        raw_result = finished_job.result or {}
        normalized = normalize_task_outputs(raw_result, task_definition)

        task_run.status = PipelineTaskRunStatus.SUCCEEDED
        task_run.result = PipelineTaskResult(
            pipeline_task_id=task_run.pipeline_task_id,
            pipeline_task_run_id=task_run.pipeline_task_run_id,
            job_type=task_run.job_type,
            job_id=finished_job.job_id,
            job_status=JobStatus.SUCCEEDED,
            refs=normalized.refs,
            summary=normalized.summary,
            metrics=normalized.metrics,
            artifacts=normalized.artifacts,
            raw_result=normalized.raw_result,
            error=None,
        )
        task_run.error = None
        task_run.finished_at = now
        task_run.updated_at = now

        saved = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()
        return saved

    def _validate_recording_contract(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
        task_run: PipelineTaskRunManifest,
        finished_job: JobManifest,
    ) -> None:
        if finished_job.status != JobStatus.SUCCEEDED:
            raise ValueError(
                f"Cannot record result for non-succeeded job: "
                f"job_id={finished_job.job_id} status={finished_job.status}"
            )
        if task_run.pipeline_run_id != pipeline_run.pipeline_run_id:
            raise ValueError(
                f"Task run pipeline_run_id mismatch: "
                f"task_run={task_run.pipeline_run_id} "
                f"pipeline={pipeline_run.pipeline_run_id}"
            )
        if task_run.pipeline_task_id != task_definition.pipeline_task_id:
            raise ValueError(
                f"Task run pipeline_task_id mismatch: "
                f"task_run={task_run.pipeline_task_id} "
                f"definition={task_definition.pipeline_task_id}"
            )
        if task_run.job_id is not None and task_run.job_id != finished_job.job_id:
            raise ValueError(
                f"Task run job_id mismatch: "
                f"task_run={task_run.job_id} "
                f"finished_job={finished_job.job_id}"
            )
