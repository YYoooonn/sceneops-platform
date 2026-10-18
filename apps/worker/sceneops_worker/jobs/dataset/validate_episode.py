from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.ids import default_validation_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.episodes.schemas import EpisodeValidationRunRecord
from sceneops_core.jobs.schemas import (
    JobType,
    ValidateEpisodeJobParams,
    ValidateEpisodeJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.derived.publication import publish_registered
from sceneops_worker.episodes.resolver import resolve_episode_revisions
from sceneops_episodes.validation import EpisodeManifestValidator
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler

_validator = EpisodeManifestValidator()


class ValidateEpisodeJobHandler(
    RunRecordHandler[
        ValidateEpisodeJobParams, ValidateEpisodeJobResult, EpisodeValidationRunRecord
    ],
    JobHandler[ValidateEpisodeJobParams, ValidateEpisodeJobResult],
):
    """Validates pinned Episode revisions; each per-episode run record pins
    the revision it assessed. Validation reads no DatasetVersion and never
    changes a unit, a revision or a membership: readiness is derived per
    revision from these run records (ADR-015 §6.4). A pin that names no
    registered revision fails the job."""

    @property
    def job_type(self) -> JobType:
        return JobType.VALIDATE_EPISODE

    @property
    def params_model(self) -> type[ValidateEpisodeJobParams]:
        return ValidateEpisodeJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {**inputs.params, "revisions": inputs.refs.get("revisions") or []}

    def build_initial_record(
        self,
        *,
        job: Any,
        params: ValidateEpisodeJobParams,
        started_at: datetime,
    ) -> EpisodeValidationRunRecord:
        return EpisodeValidationRunRecord(
            run_id=default_validation_run_id(job.job_id),
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_task_run_id=job.pipeline_task_run_id,
            job_id=job.job_id,
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: ValidateEpisodeJobParams,
        context: WorkerContext,
        initial_record: EpisodeValidationRunRecord,
        started_at: datetime,
    ) -> tuple[EpisodeValidationRunRecord, ValidateEpisodeJobResult]:
        run_id = initial_record.run_id
        total_issues = 0
        total_blocking = 0
        total_warnings = 0
        blocking = False
        report_episodes: list[dict] = []

        for resolved in await resolve_episode_revisions(context, params.revisions):
            record = resolved.record
            episode_id = record.episode_id
            result = _validator.validate(record=record, manifest=resolved.manifest)

            episode_issue_count = len(result.issues)
            episode_blocking_count = sum(1 for i in result.issues if i.blocking)
            episode_warning_count = episode_issue_count - episode_blocking_count

            total_issues += episode_issue_count
            total_blocking += episode_blocking_count
            total_warnings += episode_warning_count
            if result.should_block:
                blocking = True

            report_episodes.append(
                {
                    "episode_id": episode_id,
                    "manifest_artifact_id": record.manifest_artifact_id,
                    "manifest_checksum": record.manifest_checksum,
                    "status": result.status,
                    "observation_count": result.observation_count,
                    "state_count": result.state_count,
                    "action_count": result.action_count,
                    "event_count": result.event_count,
                    "issues": [i.model_dump(mode="json") for i in result.issues],
                }
            )

            per_episode_run_id = _per_episode_validation_run_id(job.job_id, episode_id)
            per_episode_report = await publish_registered(
                context,
                kind=ArtifactKind.EPISODE_VALIDATION_REPORT,
                prefix="valreport",
                logical_id=per_episode_run_id,
                directory=context.artifact_store.join_uri(
                    context.settings.run_root_uri,
                    "episode_validations",
                    per_episode_run_id,
                ),
                stem="report",
                data=canonical_json_bytes(
                    {
                        "run_id": per_episode_run_id,
                        "job_id": job.job_id,
                        "episode_id": episode_id,
                        "checked_episode_count": 1,
                        "total_issues": episode_issue_count,
                        "should_block_pipeline": result.should_block,
                        "status": result.status,
                        "episodes": [report_episodes[-1]],
                    }
                ),
                media_type="application/json",
                owner_type=ArtifactOwnerType.EPISODE_VALIDATION_RUN,
                owner_id=per_episode_run_id,
                run_id=per_episode_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )
            await context.runs.episode_runs.upsert(
                EpisodeValidationRunRecord(
                    run_id=per_episode_run_id,
                    episode_id=episode_id,
                    manifest_artifact_id=record.manifest_artifact_id,
                    manifest_checksum=record.manifest_checksum,
                    status=RunStatus.SUCCEEDED,
                    validation_status=result.status,
                    should_block_pipeline=result.should_block,
                    validation_report_uri=per_episode_report.uri,
                    checked_episode_count=1,
                    issue_count=episode_issue_count,
                    error_count=episode_blocking_count,
                    warning_count=episode_warning_count,
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_task_run_id=job.pipeline_task_run_id,
                    job_id=job.job_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )

        if blocking:
            overall_status = "failed"
        elif total_issues > 0:
            overall_status = "warning"
        else:
            overall_status = "ready"

        report = await publish_registered(
            context,
            kind=ArtifactKind.EPISODE_VALIDATION_REPORT,
            prefix="valreport",
            logical_id=run_id,
            directory=context.artifact_store.join_uri(
                context.settings.run_root_uri, "episode_validations", run_id
            ),
            stem="report",
            data=canonical_json_bytes(
                {
                    "run_id": run_id,
                    "job_id": job.job_id,
                    "checked_episode_count": len(params.revisions),
                    "total_issues": total_issues,
                    "should_block_pipeline": blocking,
                    "status": overall_status,
                    "episodes": report_episodes,
                }
            ),
            media_type="application/json",
            owner_type=ArtifactOwnerType.EPISODE_VALIDATION_RUN,
            owner_id=run_id,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )
        report_uri = report.uri

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "validation_status": overall_status,
                "should_block_pipeline": blocking,
                "validation_report_uri": report_uri,
                "checked_episode_count": len(params.revisions),
                "issue_count": total_issues,
                "error_count": total_blocking,
                "warning_count": total_warnings,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, ValidateEpisodeJobResult(
            status=overall_status,
            should_block_pipeline=blocking,
            checked_episode_count=len(params.revisions),
            issue_count=total_issues,
            validation_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: EpisodeValidationRunRecord
    ) -> EpisodeValidationRunRecord:
        return await context.runs.episode_runs.upsert(record)


def _per_episode_validation_run_id(job_id: str, episode_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{episode_id}".encode()).hexdigest()[:12]
    return f"val-episode-{digest}"
