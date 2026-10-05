"""A mocked WorkerContext holding registered Episodes: EpisodeRecords, their
EPISODE_MANIFEST ArtifactRecords and the manifest bytes behind them."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeRecord,
    load_canonical_episode_manifest,
    project_episode_record,
)
from sceneops_core.episodes.testing import action, episode_manifest, observation, state
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType

from sceneops_worker.episodes.artifacts import EpisodeManifestIntegrityError

S = 1_000_000_000


def sample_manifest(unit_key: str = "recording") -> EpisodeManifest:
    return episode_manifest(
        [
            observation("/cam", 0),
            state("/odom", S, x=1.0),
            state("/odom", 2 * S, x=2.0),
            action("/control", 2 * S, steering=0.1),
        ],
        unit_key=unit_key,
        window=(0, 3 * S),
    )


def register(manifest: EpisodeManifest, *, dataset_id="d1", version="v1"):
    data = manifest.to_canonical_bytes()
    checksum = manifest.checksum()
    record = project_episode_record(
        dataset_id=dataset_id,
        dataset_version=version,
        manifest=manifest,
        manifest_artifact_id=f"episode-manifest-{manifest.lineage.source.unit_key}",
        manifest_checksum=checksum,
    )
    artifact = ArtifactRecord(
        artifact_id=record.manifest_artifact_id,
        kind=ArtifactKind.EPISODE_MANIFEST,
        uri=f"mem://episodes/{record.episode_id}/manifest.json",
        checksum=checksum,
        size_bytes=len(data),
        media_type="application/json",
        owner_type=ArtifactOwnerType.EPISODE,
        owner_id=record.episode_id,
    )
    return record, artifact, data


def make_context(
    registered: list[tuple[EpisodeRecord, ArtifactRecord, bytes]],
) -> MagicMock:
    records = {r.episode_id: r for r, _, _ in registered}
    artifacts = {a.artifact_id: a for _, a, _ in registered}
    blobs = {a.uri: d for _, a, d in registered}

    async def read_bytes(*, uri, checksum, size_bytes=None):
        data = blobs.get(uri)
        if data is None:
            raise EpisodeManifestIntegrityError(f"episode manifest not found: {uri}")
        import hashlib

        if "sha256:" + hashlib.sha256(data).hexdigest() != checksum:
            raise EpisodeManifestIntegrityError("checksum mismatch")
        return data

    async def read_manifest(**kwargs):
        return load_canonical_episode_manifest(await read_bytes(**kwargs))

    context = MagicMock()
    context.blobs = blobs
    context.episode_store.get = AsyncMock(side_effect=lambda eid: records.get(eid))
    context.artifact_record_store.get = AsyncMock(
        side_effect=lambda aid: artifacts.get(aid)
    )
    context.artifact_record_store.create = AsyncMock()
    context.artifact_record_store.register = AsyncMock(return_value=(MagicMock(), True))
    context.episode_artifact_store.read_pinned_manifest_bytes = AsyncMock(
        side_effect=read_bytes
    )
    context.episode_artifact_store.read_pinned_manifest = AsyncMock(
        side_effect=read_manifest
    )
    context.artifact_store.join_uri = MagicMock(
        side_effect=lambda *parts: "/".join(parts)
    )
    context.artifact_store.write_json = AsyncMock()
    context.runs.episode_runs.upsert = AsyncMock(side_effect=lambda r: r)
    context.settings.run_root_uri = "mem://runs"
    context.commit = AsyncMock()
    context.rollback = AsyncMock()
    return context


def job(job_type: JobType, params: dict) -> JobManifest:
    return JobManifest(
        job_id="job-1",
        type=job_type,
        status=JobStatus.RUNNING,
        params=params,
    )
