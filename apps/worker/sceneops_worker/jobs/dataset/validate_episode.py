from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_validation_run_id, generate_artifact_id
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
from sceneops_worker.episodes.validation import EpisodeManifestValidator
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler

_validator = EpisodeManifestValidator()


class ValidateEpisodeJobHandler(
    RunRecordHandler[
        ValidateEpisodeJobParams, ValidateEpisodeJobResult, EpisodeValidationRunRecord
    ],
    JobHandler[ValidateEpisodeJobParams, ValidateEpisodeJobResult],
):
    """EpisodeRecord + EpisodeManifest -> structural usability check.

    Mirrors ValidateSceneJobHandler's job-level + per-item run-record split,
    but keyed by episode_id (register_episode's REF output), not manifest
    URI — EpisodeRecord already carries its own episode_manifest_uri once
    registered. Does not update DatasetVersion summary — no concrete reader
    needs an Episode-domain quality cache yet (SceneOps V2 Request 17 §8).
    """

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
            dataset_id=job.params.get("dataset_id"),
            dataset_version=job.params.get("dataset_version"),
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
        dataset_id = job.params.get("dataset_id")
        dataset_version = job.params.get("dataset_version")

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
            record = await context.episode_store.get(episode_id)
            if record is None:
                total_issues += 1
                total_blocking += 1
                blocking = True
                report_episodes.append(
                    {"episode_id": episode_id, "error": "episode_not_found"}
                )
                continue

            manifest = None
            if record.episode_manifest_uri:
                manifest = await context.episode_artifact_store.load_episode_manifest(
                    record.episode_manifest_uri
                )

            result = _validator.validate(record=record, manifest=manifest)

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
                    "status": result.status,
                    "frame_count": result.frame_count,
                    "observation_channels": result.observation_channels,
                    "action_channels": result.action_channels,
                    "issues": [i.model_dump() for i in result.issues],
                }
            )

            per_episode_run_id = _per_episode_validation_run_id(job.job_id, episode_id)
            per_episode_report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "episode_validations",
                per_episode_run_id,
                "report.json",
            )
            per_episode_report = {
                "run_id": per_episode_run_id,
                "job_id": job.job_id,
                "episode_id": episode_id,
                "checked_episode_count": 1,
                "total_issues": episode_issue_count,
                "should_block_pipeline": result.should_block,
                "status": result.status,
                "episodes": [report_episodes[-1]],
                "created_at": utc_now().isoformat(),
            }
            await context.artifact_store.write_json(
                per_episode_report_uri, per_episode_report
            )

            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.EPISODE_VALIDATION_REPORT,
                    uri=per_episode_report_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.EPISODE_VALIDATION_RUN,
                owner_id=per_episode_run_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=per_episode_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

            per_episode_record = EpisodeValidationRunRecord(
                run_id=per_episode_run_id,
                episode_id=episode_id,
                episode_manifest_uri=record.episode_manifest_uri,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=RunStatus.SUCCEEDED,
                validation_status=result.status,
                should_block_pipeline=result.should_block,
                validation_report_uri=per_episode_report_uri,
                issue_count=episode_issue_count,
                error_count=episode_blocking_count,
                warning_count=episode_warning_count,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_task_run_id=job.pipeline_task_run_id,
                job_id=job.job_id,
                started_at=started_at,
                finished_at=utc_now(),
            )
            await context.runs.episode_runs.upsert(per_episode_record)

        if blocking:
            overall_status = "failed"
        elif total_issues > 0:
            overall_status = "warning"
        else:
            overall_status = "ready"

        report = {
            "run_id": run_id,
            "job_id": job.job_id,
            "checked_episode_count": len(episode_ids),
            "total_issues": total_issues,
            "should_block_pipeline": blocking,
            "status": overall_status,
            "episodes": report_episodes,
            "created_at": utc_now().isoformat(),
        }
        report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri,
            "episode_validations",
            run_id,
            "report.json",
        )
        await context.artifact_store.write_json(report_uri, report)

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.EPISODE_VALIDATION_REPORT,
                uri=report_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.EPISODE_VALIDATION_RUN,
            owner_id=run_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

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
