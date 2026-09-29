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

    Also the sole writer of ``DatasetVersionRecord.episode.episode_count``:
    this is the point in the pipeline where EpisodeRecord rows actually
    become canonical (``BuildEpisodesJobHandler`` runs first but only
    produces manifests/artifacts, never an EpisodeRecord). The count is
    always *recomputed* from a live ``EpisodeRepository.count()`` query
    after this call's upserts are flushed, never incremented from this
    dispatch's own ``registered_episode_count`` — a caller that dispatches
    register_episode once per source scene (as
    scripts/canonical/canonical_bootstrap.sh does) must still converge on
    the true total, not just "however many this one dispatch touched".
    Recomputing (rather than trusting a delta) is also what keeps this
    correct under retry/upsert of an already-registered episode.

    Concurrency: recompute-then-write is only safe against two *sequential*
    dispatches on its own -- two independent dispatches whose count+write
    steps genuinely overlap in time could otherwise both compute the same
    stale count and both write it (lost update). Each touched DatasetVersion
    is therefore locked (``dataset_store.lock_version_for_update`` — a real
    ``SELECT ... FOR UPDATE`` row lock, see
    ``PostgresDatasetVersionRepository.lock_for_update``'s own docstring)
    immediately before the count+write pair, so at most one dispatch's
    count+write section runs against a given DatasetVersion at a time; a
    concurrent dispatch blocks at the lock until the first commits, then
    sees its committed episode row(s) in its own count. ``touched_versions``
    is iterated in sorted order so two dispatches that (atypically) touch
    more than one DatasetVersion each always acquire locks in the same
    global order, ruling out a lock-ordering deadlock between them.
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
        touched_versions: set[tuple[str, str]] = set()

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
                if ds_id and ds_version:
                    touched_versions.add((ds_id, ds_version))
                continue

            await context.episode_store.upsert(record)

            registered_ids.append(episode_id)
            registered_uris.append(uri)
            if ds_id and ds_version:
                touched_versions.add((ds_id, ds_version))

        # Refresh each touched DatasetVersion's Episode summary from live
        # canonical membership -- same session, so this observes the
        # upserts just flushed above -- before the commit below makes it
        # durable. See this handler's own docstring for why this is a
        # recompute (not an increment) AND why it's lock-then-count-then-
        # write (not just recompute) -- sorted for a consistent global lock
        # order across concurrent dispatches.
        for ds_id, ds_version in sorted(touched_versions):
            await context.dataset_store.lock_version_for_update(
                dataset_id=ds_id, version=ds_version
            )
            episode_count = await context.episode_store.count(
                dataset_id=ds_id, dataset_version=ds_version
            )
            await context.dataset_store.update_episode_summary(
                dataset_id=ds_id, version=ds_version, episode_count=episode_count
            )

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
