"""PipelineTaskResultRecorder — persists the result of a finished pipeline task.

Owns the RUNNING → SUCCEEDED state transition and writes a normalized
PipelineTaskResult into PipelineTaskRun.result.

Normalization:
  refs       — downstream-input-oriented IDs and URIs (PipelineInputResolver reads these)
  summary    — counts, status flags, and metric summaries
  raw_result — full handler output preserved for debugging

Alias normalization resolves generic handler keys (e.g. report_uri) to stable
pipeline-level ref keys (e.g. validation_report_uri) using job_type context.
PipelineInputResolver.resolve() reads these refs directly — no context propagation needed.
"""

from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineTaskDefinition,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_worker.core.context import WorkerContext

# Keys extracted into refs — IDs and URIs consumed by downstream tasks
_REFS_KEYS: frozenset[str] = frozenset(
    {
        "dataset_manifest_uri",
        "scene_manifest_uris",
        "validation_run_id",
        "validation_report_uri",
        "profile_run_id",
        "profile_report_uri",
        "inference_run_id",
        "predictions_root_uri",
        "prediction_manifest_uri",
        "evaluation_run_id",
        "metrics_uri",
        "evaluation_manifest_uri",
    }
)

# Keys extracted into summary — counts, status flags, and metric summaries
_SUMMARY_KEYS: frozenset[str] = frozenset(
    {
        "scene_count",
        "sample_count",
        "frame_count",
        "validation_status",
        "should_block_pipeline",
        "checked_scene_count",
        "issue_count",
        "annotation_count",
        "prediction_count",
        "ground_truth_count",
        "evaluation_unit",
        "primary_metric_name",
        "primary_metric_value",
        "status",
        "observed_channels",
        "sensor_coverage_ratio",
    }
)

# report_uri is a generic handler key; map it to the job-type-specific pipeline ref.
# Handlers return report_uri; the pipeline layer needs the stable namespaced key.
_REPORT_URI_ALIAS: dict[str, str] = {
    JobType.VALIDATE_SCENE: "validation_report_uri",
    JobType.PROFILE_SCENE: "profile_report_uri",
}


def _normalize_job_result(
    job_result: JsonDict | None,
    job_type: JobType | str | None = None,
) -> tuple[JsonDict, JsonDict, JsonDict]:
    """Split job_result into (refs, summary, raw_result).

    job_type is used to resolve generic handler keys to stable pipeline-level refs:
      - report_uri → validation_report_uri  (VALIDATE_SCENE)
      - report_uri → profile_report_uri     (PROFILE_SCENE)
    raw_result is the unmodified handler output kept for debugging.
    """
    if not job_result:
        return {}, {}, {}

    refs: JsonDict = {}
    summary: JsonDict = {}

    for key, value in job_result.items():
        if value is None:
            continue
        if key in _REFS_KEYS:
            refs[key] = value
        elif key in _SUMMARY_KEYS:
            summary[key] = value

    # Alias report_uri → job-type-specific stable pipeline ref key.
    # Handlers use the generic report_uri; the pipeline layer uses namespaced refs.
    report_uri = job_result.get("report_uri")
    if report_uri is not None and job_type is not None:
        alias_key = _REPORT_URI_ALIAS.get(str(job_type))
        if alias_key and alias_key not in refs:
            refs[alias_key] = report_uri

    return refs, summary, dict(job_result)


class PipelineTaskResultRecorder:
    """Persists a finished pipeline task run result to the DB.

    Called by PipelineTaskRunner after JobRunner completes.
    Normalizes raw job handler output into pipeline-level refs/summary that
    PipelineInputResolver can read directly for downstream task input resolution.
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
        now = utc_now()

        refs, summary, raw_result = _normalize_job_result(
            finished_job.result,
            job_type=task_run.job_type,
        )

        task_run.status = PipelineTaskRunStatus.SUCCEEDED
        task_run.result = PipelineTaskResult(
            pipeline_task_id=task_run.pipeline_task_id,
            pipeline_task_run_id=task_run.pipeline_task_run_id,
            job_type=task_run.job_type,
            job_id=finished_job.job_id,
            job_status=JobStatus.SUCCEEDED,
            refs=refs,
            summary=summary,
            raw_result=raw_result,
            error=None,
        )
        task_run.error = None
        task_run.finished_at = now
        task_run.updated_at = now

        saved = await self._context.pipeline_store.save_task(task_run)
        await self._context.commit()
        return saved
