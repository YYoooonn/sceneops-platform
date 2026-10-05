"""EpisodeService over in-memory fakes: list / get, the verified current
manifest revision, and revision-pinned quality (ADR-007 §13.4, §14.4)."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeValidationRunRecord,
    project_episode_record,
)
from sceneops_core.episodes.testing import action, episode_manifest, state
from sceneops_core.runs.schemas import RunStatus

from app.domains.episodes.schemas import EpisodeQualityReadiness
from app.domains.episodes.service import EpisodeManifestUnavailableError, EpisodeService


def _registered(unit_key="recording", robot_run_id="run-1", version="v1"):
    manifest = episode_manifest(
        [state("/odom", 0, x=1.0), action("/control", 5, u=0.1)],
        unit_key=unit_key,
        robot_run_id=robot_run_id,
    )
    data = manifest.to_canonical_bytes()
    record = project_episode_record(
        dataset_id="d1",
        dataset_version=version,
        manifest=manifest,
        manifest_artifact_id=f"episode-manifest-{unit_key}-{robot_run_id}",
        manifest_checksum=manifest.checksum(),
    )
    artifact = ArtifactRecord(
        artifact_id=record.manifest_artifact_id,
        kind=ArtifactKind.EPISODE_MANIFEST,
        uri=f"mem://{record.episode_id}",
        checksum=manifest.checksum(),
        size_bytes=len(data),
        owner_type=ArtifactOwnerType.EPISODE,
        owner_id=record.episode_id,
    )
    return record, artifact, data


class FakeEpisodes:
    def __init__(self, records: list[EpisodeRecord]) -> None:
        self.records = {r.episode_id: r for r in records}

    async def get(self, episode_id):
        return self.records.get(episode_id)

    async def list(
        self,
        *,
        dataset_id=None,
        dataset_version=None,
        robot_run_id=None,
        limit=100,
        offset=0,
    ):
        items = [
            r
            for r in sorted(self.records.values(), key=lambda r: r.episode_id)
            if (dataset_id is None or r.dataset_id == dataset_id)
            and (dataset_version is None or r.dataset_version == dataset_version)
            and (robot_run_id is None or r.robot_run_id == robot_run_id)
        ]
        return items[offset : offset + limit]


class FakeArtifacts:
    def __init__(self, artifacts) -> None:
        self.artifacts = {a.artifact_id: a for a in artifacts}

    async def get(self, artifact_id):
        return self.artifacts.get(artifact_id)


class FakeStore:
    def __init__(self, blobs) -> None:
        self.blobs = blobs

    async def read_bytes(self, uri):
        return self.blobs[uri]


class FakeRuns:
    def __init__(self, runs) -> None:
        self.runs = runs

    async def list(
        self,
        *,
        type=None,
        status=None,
        episode_id=None,
        manifest_artifact_id=None,
        limit=100,
        **_,
    ):
        return [
            r
            for r in reversed(self.runs)
            if (type is None or r.type == type)
            and (status is None or r.status == status)
            and (episode_id is None or r.episode_id == episode_id)
            and (
                manifest_artifact_id is None
                or r.manifest_artifact_id == manifest_artifact_id
            )
        ][:limit]


def _service(registered, runs=()):
    return EpisodeService(
        repository=FakeEpisodes([r for r, _, _ in registered]),
        run_repository=FakeRuns(list(runs)),
        artifact_repository=FakeArtifacts([a for _, a, _ in registered]),
        artifact_store=FakeStore({a.uri: d for _, a, d in registered}),
    )


def _validation(record, status="ready", **pin):
    return EpisodeValidationRunRecord(
        run_id=f"val-{status}-{pin.get('manifest_artifact_id', record.manifest_artifact_id)}",
        status=RunStatus.SUCCEEDED,
        episode_id=record.episode_id,
        manifest_artifact_id=pin.get(
            "manifest_artifact_id", record.manifest_artifact_id
        ),
        manifest_checksum=pin.get("manifest_checksum", record.manifest_checksum),
        validation_status=status,
        should_block_pipeline=status == "failed",
    )


async def test_list_filters_and_paginates() -> None:
    registered = [
        _registered("a"),
        _registered("b", robot_run_id="run-2"),
        _registered("c", version="v2"),
    ]
    service = _service(registered)
    assert (await service.list_episodes(dataset_version="v1")).count == 2
    assert (await service.list_episodes(robot_run_id="run-2")).count == 1
    assert (await service.list_episodes(limit=1, offset=1)).count == 1


async def test_get_episode() -> None:
    record, artifact, data = _registered()
    service = _service([(record, artifact, data)])
    assert (await service.get_episode(record.episode_id)).episode == record
    assert await service.get_episode("episode-missing") is None


async def test_manifest_is_the_verified_current_revision() -> None:
    record, artifact, data = _registered()
    service = _service([(record, artifact, data)])
    response = await service.get_episode_manifest(record.episode_id)
    assert response.manifest_artifact_id == record.manifest_artifact_id
    assert response.manifest["actions"][0]["values"] == {"u": 0.1}
    assert await service.get_episode_manifest("episode-missing") is None


async def test_manifest_with_changed_bytes_is_refused() -> None:
    record, artifact, data = _registered()
    service = _service([(record, artifact, data + b" ")])
    with pytest.raises(EpisodeManifestUnavailableError):
        await service.get_episode_manifest(record.episode_id)


async def test_quality_counts_only_the_current_revision() -> None:
    record, artifact, data = _registered()
    old_run = _validation(
        record,
        "ready",
        manifest_artifact_id="episode-manifest-old",
        manifest_checksum="sha256:" + "0" * 64,
    )
    service = _service([(record, artifact, data)], runs=[old_run])
    quality = await service.get_episode_quality(record.episode_id)
    assert quality.readiness == EpisodeQualityReadiness.UNKNOWN
    assert quality.blocking_reasons == ["validation_missing"]

    service = _service([(record, artifact, data)], runs=[old_run, _validation(record)])
    quality = await service.get_episode_quality(record.episode_id)
    assert quality.readiness == EpisodeQualityReadiness.READY
    assert quality.counts.state_count == 1 and quality.counts.action_count == 1


async def test_quality_blocked_and_profile_summary() -> None:
    record, artifact, data = _registered()
    profile = EpisodeProfileRunRecord(
        run_id="prof-1",
        status=RunStatus.SUCCEEDED,
        episode_id=record.episode_id,
        manifest_artifact_id=record.manifest_artifact_id,
        manifest_checksum=record.manifest_checksum,
        action_count=1,
        window_duration_ns=6,
    )
    service = _service(
        [(record, artifact, data)], runs=[_validation(record, "failed"), profile]
    )
    quality = await service.get_episode_quality(record.episode_id)
    assert quality.readiness == EpisodeQualityReadiness.BLOCKED
    assert quality.profile.window_duration_ns == 6
    assert await service.get_episode_quality("episode-missing") is None
