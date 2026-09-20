from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_profile_run_id, generate_artifact_id
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
from sceneops_worker.episodes.profiling import EpisodeManifestProfiler
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler

_profiler = EpisodeManifestProfiler()


class ProfileEpisodeJobHandler(
    RunRecordHandler[
        ProfileEpisodeJobParams, ProfileEpisodeJobResult, EpisodeProfileRunRecord
    ],
    JobHandler[ProfileEpisodeJobParams, ProfileEpisodeJobResult],
):
    """EpisodeManifest -> descriptive trajectory/data profile.

    Pure description, no usability judgment — that's ValidateEpisodeJobHandler's
    job. Mirrors ProfileSceneJobHandler's job-level + per-item run-record
    split, keyed by episode_id like validate_episode (SceneOps V2 Request 17).
    """

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
        params: ProfileEpisodeJobParams,
        context: WorkerContext,
        initial_record: EpisodeProfileRunRecord,
        started_at: datetime,
    ) -> tuple[EpisodeProfileRunRecord, ProfileEpisodeJobResult]:
        run_id = initial_record.run_id
        episode_ids = _resolve_episode_ids(params)
        dataset_id = job.params.get("dataset_id")
        dataset_version = job.params.get("dataset_version")

        if not episode_ids:
            raise ValueError("profile_episode requires at least one episode_id.")

        total_frames = 0
        total_observations = 0
        total_actions = 0
        all_observation_channels: set[str] = set()
        all_action_channels: set[str] = set()
        episode_profiles: list[dict] = []

        for episode_id in episode_ids:
            record = await context.episode_store.get(episode_id)
            if record is None or not record.episode_manifest_uri:
                continue

            manifest = await context.episode_artifact_store.load_episode_manifest(
                record.episode_manifest_uri
            )
            if manifest is None:
                continue

            result = _profiler.profile(manifest=manifest)

            all_observation_channels.update(result.observation_channels)
            all_action_channels.update(result.action_channels)
            total_frames += result.frame_count
            total_observations += result.observation_count
            total_actions += result.action_count

            episode_profiles.append(
                {
                    "episode_id": episode_id,
                    "frame_count": result.frame_count,
                    "observation_count": result.observation_count,
                    "action_count": result.action_count,
                    "observation_channels": result.observation_channels,
                    "action_channels": result.action_channels,
                    "control_frequency_hz": result.control_frequency_hz,
                    "duration_us": result.duration_us,
                    "task": result.task,
                    "outcome": result.outcome,
                    "mission_id": result.mission_id,
                }
            )

            per_episode_run_id = _per_episode_profile_run_id(job.job_id, episode_id)
            per_episode_report_uri = context.artifact_store.join_uri(
                context.settings.run_root_uri,
                "episode_profiles",
                per_episode_run_id,
                "report.json",
            )
            per_episode_report = {
                "run_id": per_episode_run_id,
                "job_id": job.job_id,
                "episode_id": episode_id,
                "created_at": utc_now().isoformat(),
                **episode_profiles[-1],
            }
            await context.artifact_store.write_json(
                per_episode_report_uri, per_episode_report
            )

            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.EPISODE_PROFILE_REPORT,
                    uri=per_episode_report_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.EPISODE_PROFILE_RUN,
                owner_id=per_episode_run_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=per_episode_run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

            per_episode_record = EpisodeProfileRunRecord(
                run_id=per_episode_run_id,
                episode_id=episode_id,
                episode_manifest_uri=record.episode_manifest_uri,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=RunStatus.SUCCEEDED,
                profile_report_uri=per_episode_report_uri,
                frame_count=result.frame_count,
                observation_count=result.observation_count,
                action_count=result.action_count,
                observation_channels=result.observation_channels,
                action_channels=result.action_channels,
                control_frequency_hz=result.control_frequency_hz,
                duration_us=result.duration_us,
                task=result.task,
                outcome=result.outcome,
                mission_id=result.mission_id,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_task_run_id=job.pipeline_task_run_id,
                job_id=job.job_id,
                started_at=started_at,
                finished_at=utc_now(),
            )
            await context.runs.episode_runs.upsert(per_episode_record)

        report = {
            "run_id": run_id,
            "job_id": job.job_id,
            "checked_episode_count": len(episode_profiles),
            "frame_count": total_frames,
            "observation_count": total_observations,
            "action_count": total_actions,
            "observation_channels": sorted(all_observation_channels),
            "action_channels": sorted(all_action_channels),
            "episodes": episode_profiles,
            "created_at": utc_now().isoformat(),
        }
        report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri, "episode_profiles", run_id, "report.json"
        )
        await context.artifact_store.write_json(report_uri, report)

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.EPISODE_PROFILE_REPORT,
                uri=report_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.EPISODE_PROFILE_RUN,
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
                "checked_episode_count": len(episode_profiles),
                "frame_count": total_frames,
                "observation_count": total_observations,
                "action_count": total_actions,
                "observation_channels": sorted(all_observation_channels),
                "action_channels": sorted(all_action_channels),
                "profile_report_uri": report_uri,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, ProfileEpisodeJobResult(
            checked_episode_count=len(episode_profiles),
            frame_count=total_frames,
            observation_count=total_observations,
            action_count=total_actions,
            observed_observation_channels=sorted(all_observation_channels),
            observed_action_channels=sorted(all_action_channels),
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
