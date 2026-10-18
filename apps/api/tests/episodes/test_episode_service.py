"""EpisodeService over in-memory fakes: units with their revisions, and the
verified manifest and quality of the revision a DatasetVersion pins
(ADR-015 §6.4, §7)."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRevisionRecord,
    EpisodeValidationRunRecord,
    episode_id_for,
    project_episode_revision,
)
from sceneops_core.episodes.testing import action, episode_manifest, state
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_core.runs.schemas import RunStatus

from app.core.revisions import RevisionAddress
from app.domains.episodes.schemas import EpisodeQualityReadiness
from app.domains.episodes.service import EpisodeManifestUnavailableError, EpisodeService


def _registered(unit_key="recording", robot_run_id="run-1", u=0.1):
    manifest = episode_manifest(
        [state("/odom", 0, x=1.0), action("/control", 5, u=u)],
        unit_key=unit_key,
        robot_run_id=robot_run_id,
    )
    data = manifest.to_canonical_bytes()
    record = project_episode_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=episode_id_for(manifest.lineage.source),
            manifest_checksum=manifest.checksum(),
        ),
        manifest_checksum=manifest.checksum(),
    )
    artifact = ArtifactRecord(
        artifact_id=record.manifest_artifact_id,
        kind=ArtifactKind.EPISODE_MANIFEST,
        uri=f"mem://{record.episode_id}/{record.manifest_checksum}",
        checksum=manifest.checksum(),
        size_bytes=len(data),
        owner_type=ArtifactOwnerType.EPISODE,
        owner_id=record.episode_id,
    )
    return record, artifact, data


class FakeEpisodes:
    """Revisions, all pinned by ``d1/v1`` unless ``members`` says otherwise."""

    def __init__(self, records: list[EpisodeRevisionRecord], members=None) -> None:
        self.records = list(records)
        self.members = members if members is not None else {("d1", "v1"): records}

    async def get(self, episode_id):
        found = await self.list_revisions(episode_id)
        return found[0].unit() if found else None

    async def list_revisions(self, episode_id):
        return [r for r in self.records if r.episode_id == episode_id]

    async def get_member_revision(self, *, dataset_id, dataset_version, episode_id):
        pinned = self.members.get((dataset_id, dataset_version), [])
        return next((r for r in pinned if r.episode_id == episode_id), None)

    async def get_revision(self, *, episode_id, manifest_checksum):
        return next(
            (
                r
                for r in self.records
                if (r.episode_id, r.manifest_checksum)
                == (episode_id, manifest_checksum)
            ),
            None,
        )

    async def list(self, *, robot_run_id=None, limit=100, offset=0):
        units = {r.episode_id: r.unit() for r in self.records}
        items = [
            units[e]
            for e in sorted(units)
            if robot_run_id is None or units[e].robot_run_id == robot_run_id
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


def _service(registered, runs=(), members=None):
    return EpisodeService(
        repository=FakeEpisodes([r for r, _, _ in registered], members),
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


V1 = RevisionAddress(dataset_id="d1", dataset_version="v1")


async def test_list_lists_units_filters_and_paginates() -> None:
    registered = [
        _registered("a"),
        _registered("a", u=0.2),  # a second revision of the same unit
        _registered("b", robot_run_id="run-2"),
        _registered("c"),
    ]
    service = _service(registered)
    assert (await service.list_episodes()).count == 3
    assert (await service.list_episodes(robot_run_id="run-2")).count == 1
    assert (await service.list_episodes(limit=1, offset=1)).count == 1


async def test_get_episode_returns_the_unit_and_every_revision() -> None:
    first = _registered()
    second = _registered(u=0.2)
    service = _service([first, second])
    detail = await service.get_episode(first[0].episode_id)
    assert detail.episode == first[0].unit()
    assert {r.manifest_checksum for r in detail.revisions} == {
        first[0].manifest_checksum,
        second[0].manifest_checksum,
    }
    assert await service.get_episode("episode-missing") is None


async def test_manifest_is_the_verified_revision_the_version_pins() -> None:
    pinned = _registered()
    other = _registered(u=0.2)
    service = _service([pinned, other], members={("d1", "v1"): [pinned[0]]})
    response = await service.get_episode_manifest(pinned[0].episode_id, V1)
    assert response.manifest_artifact_id == pinned[0].manifest_artifact_id
    assert response.manifest["actions"][0]["values"] == {"u": 0.1}
    assert await service.get_episode_manifest("episode-missing", V1) is None
    assert (
        await service.get_episode_manifest(
            pinned[0].episode_id, RevisionAddress(dataset_id="d1", dataset_version="v2")
        )
        is None
    )
    # Addressed by its checksum, a revision no DatasetVersion pins is read too.
    unpinned = await service.get_episode_manifest(
        other[0].episode_id,
        RevisionAddress(manifest_checksum=other[0].manifest_checksum),
    )
    assert unpinned.manifest_artifact_id == other[0].manifest_artifact_id
    assert unpinned.manifest["actions"][0]["values"] == {"u": 0.2}


async def test_manifest_with_changed_bytes_is_refused() -> None:
    record, artifact, data = _registered()
    service = _service([(record, artifact, data + b" ")])
    with pytest.raises(EpisodeManifestUnavailableError):
        await service.get_episode_manifest(record.episode_id, V1)


async def test_quality_counts_only_the_pinned_revision() -> None:
    record, artifact, data = _registered()
    other = "sha256:" + "0" * 64
    old_run = _validation(
        record,
        "ready",
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=record.episode_id, manifest_checksum=other
        ),
        manifest_checksum=other,
    )
    service = _service([(record, artifact, data)], runs=[old_run])
    quality = await service.get_episode_quality(record.episode_id, V1)
    assert quality.readiness == EpisodeQualityReadiness.UNKNOWN
    assert quality.blocking_reasons == ["validation_missing"]

    service = _service([(record, artifact, data)], runs=[old_run, _validation(record)])
    quality = await service.get_episode_quality(record.episode_id, V1)
    assert (quality.dataset_id, quality.dataset_version) == ("d1", "v1")
    assert quality.readiness == EpisodeQualityReadiness.READY
    assert quality.counts.state_count == 1 and quality.counts.action_count == 1

    by_revision = await service.get_episode_quality(
        record.episode_id, RevisionAddress(manifest_checksum=record.manifest_checksum)
    )
    assert (by_revision.dataset_id, by_revision.dataset_version) == (None, None)
    assert by_revision.readiness == EpisodeQualityReadiness.READY


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
    quality = await service.get_episode_quality(record.episode_id, V1)
    assert quality.readiness == EpisodeQualityReadiness.BLOCKED
    assert quality.profile.window_duration_ns == 6
    assert await service.get_episode_quality("episode-missing", V1) is None
