from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeRecord, EpisodeStatus
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterEpisodeJobParams,
    RegisterEpisodeJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class RegisterEpisodeJobHandler(
    JobHandler[RegisterEpisodeJobParams, RegisterEpisodeJobResult]
):
    """Manifest -> EpisodeRecord.

    Does not register an ArtifactRecord for the manifest — that's
    ``BuildEpisodesJobHandler``'s job, since it's the one that physically
    writes the manifest file and has the producing job_id/pipeline_run_id
    (see SceneOps V2 Request 15). This handler is a pure consumer: read the
    manifest, upsert EpisodeRecord, respect replace_existing.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_EPISODE

    @property
    def params_model(self) -> type[RegisterEpisodeJobParams]:
        return RegisterEpisodeJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        episode_manifest_uris = inputs.refs.get("episode_manifest_uris") or []
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "episode_manifest_uris": episode_manifest_uris,
        }

    async def run(
        self,
        request: JobHandlerRequest[RegisterEpisodeJobParams],
    ) -> RegisterEpisodeJobResult:
        params = request.params
        context = request.context

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        registered_ids: list[str] = []
        registered_uris: list[str] = []

        for uri in params.episode_manifest_uris:
            manifest = await context.episode_artifact_store.load_episode_manifest(uri)
            if manifest is None:
                continue

            episode_id = manifest.episode_id
            ds_id = dataset_id or manifest.dataset_id
            ds_version = dataset_version or manifest.dataset_version

            record = _build_episode_record_from_manifest(
                episode_id=episode_id,
                dataset_id=ds_id,
                dataset_version=ds_version,
                manifest_uri=uri,
                manifest=manifest,
            )

            existing = await context.episode_store.get(episode_id)

            if existing is not None and not params.replace_existing:
                registered_ids.append(episode_id)
                registered_uris.append(uri)
                continue

            await context.episode_store.upsert(record)

            registered_ids.append(episode_id)
            registered_uris.append(uri)

        await context.commit()

        registered_count = len(registered_ids)

        return RegisterEpisodeJobResult(
            episode_ids=registered_ids,
            episode_manifest_uris=registered_uris,
            registered_episode_count=registered_count,
            registered=registered_count > 0,
        )


def _us_to_datetime(timestamp_us: int | None) -> datetime | None:
    """Project a manifest timestamp_us (epoch microseconds) onto a
    timezone-aware UTC datetime for EpisodeRecord's DB-facing columns.

    Integer arithmetic (not timestamp_us / 1e6) to avoid float-precision
    loss at microsecond resolution for large epoch values. EpisodeManifest
    stays the authoritative source — this is a lossless projection for
    query/API/indexed access, not a new clock or a re-derivation.
    """
    if timestamp_us is None:
        return None
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        microseconds=timestamp_us
    )


def _build_episode_record_from_manifest(
    *,
    episode_id: str,
    dataset_id: str | None,
    dataset_version: str | None,
    manifest_uri: str,
    manifest: EpisodeManifest,
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        raw_log_id=manifest.lineage.raw_log_id,
        robot_id=manifest.lineage.robot_id,
        robot_run_id=manifest.lineage.robot_run_id,
        mission_id=manifest.lineage.mission_id,
        status=EpisodeStatus.REGISTERED,
        task=manifest.task,
        outcome=manifest.outcome,
        episode_manifest_uri=manifest_uri,
        observation_channels=manifest.observation_channels,
        action_channels=manifest.action_channels,
        control_frequency_hz=manifest.control_frequency_hz,
        frame_count=manifest.frame_count,
        started_at=_us_to_datetime(manifest.start_timestamp_us),
        ended_at=_us_to_datetime(manifest.end_timestamp_us),
        metadata=manifest.metadata,
    )
