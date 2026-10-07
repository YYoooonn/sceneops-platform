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
from sceneops_worker.episodes.resolver import resolve_registered_episode
from sceneops_worker.episodes.validation import EpisodeManifestValidator
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler

_validator = EpisodeManifestValidator()


class ValidateEpisodeJobHandler(
    RunRecordHandler[
        ValidateEpisodeJobParams, ValidateEpisodeJobResult, EpisodeValidationRunRecord
    ],
    JobHandler[ValidateEpisodeJobParams, ValidateEpisodeJobResult],
):
    """Validates registered Episodes at the revision each EpisodeRecord
    points to when the job reads it; each per-episode run record pins that
    revision. Validation never changes membership or a record and caches
    nothing on the DatasetVersion: readiness is derived from these run
    records for the current revision (ADR-007 §13.4, §17.5). An episode id
    that is not registered fails the job."""

    @property
    def job_type(self) -> JobType:
        return JobType.VALIDATE_EPISODE

    @property
    def params_model(self) -> type[ValidateEpisodeJobParams]:
        return ValidateEpisodeJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        episode_ids = inputs.refs.get("episode_ids") or []
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "episode_ids": episode_ids,
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: ValidateEpisodeJobParams,
        started_at: datetime,
    ) -> EpisodeValidationRunRecord:
        return EpisodeValidationRunRecord(
            run_id=default_validation_run_id(job.job_id),
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
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
        episode_ids = _resolve_episode_ids(params)
        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        if not episode_ids:
            failed_record = initial_record.model_copy(
                update={
                    "status": RunStatus.SUCCEEDED,
                    "validation_status": "failed",
                    "should_block_pipeline": True,
                    "checked_episode_count": 0,
                    "issue_count": 1,
                    "error_count": 1,
                    "warning_count": 0,
                    "finished_at": utc_now(),
                }
            )
            return failed_record, ValidateEpisodeJobResult(
                status="failed",
                should_block_pipeline=True,
                checked_episode_count=0,
                issue_count=1,
                metadata={
                    "issues": [
                        {
                            "code": "empty_episode_input",
                            "message": (
                                "validate_episode requires at least one episode_id."
                            ),
                        }
                    ]
                },
            )

        total_issues = 0
        total_blocking = 0
        total_warnings = 0
        blocking = False
        report_episodes: list[dict] = []

        for episode_id in episode_ids:
            resolved = await resolve_registered_episode(context, episode_id)
            record = resolved.record
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
                dataset_id=dataset_id,
                dataset_version=dataset_version,
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
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
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
                    "checked_episode_count": len(episode_ids),
                    "total_issues": total_issues,
                    "should_block_pipeline": blocking,
                    "status": overall_status,
                    "episodes": report_episodes,
                }
            ),
            media_type="application/json",
            owner_type=ArtifactOwnerType.EPISODE_VALIDATION_RUN,
            owner_id=run_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
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
                "checked_episode_count": len(episode_ids),
                "issue_count": total_issues,
                "error_count": total_blocking,
                "warning_count": total_warnings,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, ValidateEpisodeJobResult(
            status=overall_status,
            should_block_pipeline=blocking,
            checked_episode_count=len(episode_ids),
            issue_count=total_issues,
            validation_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: EpisodeValidationRunRecord
    ) -> EpisodeValidationRunRecord:
        return await context.runs.episode_runs.upsert(record)


def _resolve_episode_ids(params: ValidateEpisodeJobParams) -> list[str]:
    if params.episode_ids:
        return params.episode_ids
    if params.episode_id:
        return [params.episode_id]
    return []


def _per_episode_validation_run_id(job_id: str, episode_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{episode_id}".encode()).hexdigest()[:12]
    return f"val-episode-{digest}"
