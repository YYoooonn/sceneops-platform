from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.ids import default_profile_run_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.episodes.schemas import EpisodeProfileRunRecord
from sceneops_core.jobs.schemas import (
    JobType,
    ProfileEpisodeJobParams,
    ProfileEpisodeJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.derived.publication import publish_registered
from sceneops_worker.episodes.profiling import EpisodeManifestProfiler
from sceneops_worker.episodes.resolver import resolve_registered_episode
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler

_profiler = EpisodeManifestProfiler()
_TOPIC_LISTS = ("observation_topics", "state_topics", "action_topics", "event_topics")


class ProfileEpisodeJobHandler(
    RunRecordHandler[
        ProfileEpisodeJobParams, ProfileEpisodeJobResult, EpisodeProfileRunRecord
    ],
    JobHandler[ProfileEpisodeJobParams, ProfileEpisodeJobResult],
):
    """Registered Episode revisions -> descriptive profiles.

    Each Episode is profiled at the manifest revision its record points to
    when the job reads it; the per-episode run record pins that revision.
    An episode id that is not registered fails the job."""

    @property
    def job_type(self) -> JobType:
        return JobType.PROFILE_EPISODE

    @property
    def params_model(self) -> type[ProfileEpisodeJobParams]:
        return ProfileEpisodeJobParams

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
        params: ProfileEpisodeJobParams,
        started_at: datetime,
    ) -> EpisodeProfileRunRecord:
        return EpisodeProfileRunRecord(
            run_id=default_profile_run_id(job.job_id),
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
        params: ProfileEpisodeJobParams,
        context: WorkerContext,
        initial_record: EpisodeProfileRunRecord,
        started_at: datetime,
    ) -> tuple[EpisodeProfileRunRecord, ProfileEpisodeJobResult]:
        run_id = initial_record.run_id
        episode_ids = _resolve_episode_ids(params)
        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        if not episode_ids:
            raise ValueError("profile_episode requires at least one episode_id.")

        totals = {
            "observation_count": 0,
            "state_count": 0,
            "action_count": 0,
            "event_count": 0,
        }
        topics: dict[str, set[str]] = {name: set() for name in _TOPIC_LISTS}
        episode_profiles: list[dict] = []

        for episode_id in episode_ids:
            resolved = await resolve_registered_episode(context, episode_id)
            record = resolved.record
            result = _profiler.profile(
                episode_id=episode_id, manifest=resolved.manifest
            )
            for name in totals:
                totals[name] += getattr(result, name)
            for name in _TOPIC_LISTS:
                topics[name].update(getattr(result, name))
            profile = {
                **result.model_dump(mode="json"),
                "manifest_artifact_id": record.manifest_artifact_id,
                "manifest_checksum": record.manifest_checksum,
            }
            episode_profiles.append(profile)

            per_episode_run_id = _per_episode_profile_run_id(job.job_id, episode_id)
            per_episode_report = await publish_registered(
                context,
                kind=ArtifactKind.EPISODE_PROFILE_REPORT,
                prefix="profreport",
                logical_id=per_episode_run_id,
                directory=context.artifact_store.join_uri(
                    context.settings.run_root_uri,
                    "episode_profiles",
                    per_episode_run_id,
                ),
                stem="report",
                data=canonical_json_bytes(
                    {"run_id": per_episode_run_id, "job_id": job.job_id, **profile}
                ),
                media_type="application/json",
                owner_type=ArtifactOwnerType.EPISODE_PROFILE_RUN,
                owner_id=per_episode_run_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=per_episode_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )
            await context.runs.episode_runs.upsert(
                EpisodeProfileRunRecord(
                    run_id=per_episode_run_id,
                    episode_id=episode_id,
                    manifest_artifact_id=record.manifest_artifact_id,
                    manifest_checksum=record.manifest_checksum,
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                    status=RunStatus.SUCCEEDED,
                    profile_report_uri=per_episode_report.uri,
                    checked_episode_count=1,
                    **{name: getattr(result, name) for name in totals},
                    **{name: getattr(result, name) for name in _TOPIC_LISTS},
                    window_duration_ns=result.window_duration_ns,
                    summary={"streams": profile["streams"]},
                    pipeline_run_id=job.pipeline_run_id,
                    pipeline_task_run_id=job.pipeline_task_run_id,
                    job_id=job.job_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            )

        observed = {name: sorted(values) for name, values in topics.items()}
        report = await publish_registered(
            context,
            kind=ArtifactKind.EPISODE_PROFILE_REPORT,
            prefix="profreport",
            logical_id=run_id,
            directory=context.artifact_store.join_uri(
                context.settings.run_root_uri, "episode_profiles", run_id
            ),
            stem="report",
            data=canonical_json_bytes(
                {
                    "run_id": run_id,
                    "job_id": job.job_id,
                    "checked_episode_count": len(episode_profiles),
                    **totals,
                    **observed,
                    "episodes": episode_profiles,
                }
            ),
            media_type="application/json",
            owner_type=ArtifactOwnerType.EPISODE_PROFILE_RUN,
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
                "checked_episode_count": len(episode_profiles),
                **totals,
                **observed,
                "profile_report_uri": report_uri,
                "finished_at": utc_now(),
            }
        )
        return succeeded_record, ProfileEpisodeJobResult(
            checked_episode_count=len(episode_profiles),
            **totals,
            **observed,
            profile_run_id=run_id,
            report_uri=report_uri,
        )

    async def _upsert(
        self, context: WorkerContext, record: EpisodeProfileRunRecord
    ) -> EpisodeProfileRunRecord:
        return await context.runs.episode_runs.upsert(record)


def _resolve_episode_ids(params: ProfileEpisodeJobParams) -> list[str]:
    if params.episode_ids:
        return params.episode_ids
    if params.episode_id:
        return [params.episode_id]
    return []


def _per_episode_profile_run_id(job_id: str, episode_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}:{episode_id}".encode()).hexdigest()[:12]
    return f"profile-episode-{digest}"
