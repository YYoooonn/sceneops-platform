"""Unit tests for SceneService: units, their revisions, and quality of the
revision a DatasetVersion pins.

Uses tiny in-memory fakes implementing the Scene/SceneRun/Artifact
repository protocols, matching this codebase's convention for API-layer
service tests (see apps/api/tests/episodes/test_episode_service.py) rather
than a FastAPI TestClient.

404/HTTP-contract behavior (missing Scene -> 404) is exercised live via the
pipeline-driven E2E instead — same rationale as the Episode service tests:
the underlying raise_not_found() helper is shared, already-proven
infrastructure, and this repo has no TestClient precedent for any domain.
list_scenes/get_scene/list_scene_artifacts all funnel through it identically
in the router, so we test the service-level "not found" signal (None) here.
"""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.provenance import manifest_artifact_id_for, unit_id_for
from sceneops_core.scenes.schemas import SceneRecord, SceneRevisionRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.core.revisions import RevisionAddress
from app.domains.scenes.service import SceneService


class FakeSceneRepository:
    """Scene revisions and the pins of one DatasetVersion, ``nuscenes/v1``
    unless a test names other members."""

    def __init__(
        self,
        revisions: list[SceneRevisionRecord] | None = None,
        members: dict[tuple[str, str], list[SceneRevisionRecord]] | None = None,
    ) -> None:
        self.revisions = list(revisions or [])
        self.members = (
            members if members is not None else {("nuscenes", "v1"): self.revisions}
        )

    async def get(self, scene_id: str) -> SceneRecord | None:
        found = await self.list_revisions(scene_id)
        return found[0].unit() if found else None

    async def list(
        self,
        *,
        robot_run_id: str | None = None,
        content_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        units = {r.scene_id: r.unit() for r in self.revisions}
        results = [units[s] for s in sorted(units)]
        if robot_run_id is not None:
            results = [u for u in results if u.robot_run_id == robot_run_id]
        if content_id is not None:
            results = [u for u in results if u.content_id == content_id]
        return results[offset : offset + limit]

    async def list_revisions(self, scene_id: str) -> list[SceneRevisionRecord]:
        return [r for r in self.revisions if r.scene_id == scene_id]

    async def get_member_revision(self, *, dataset_id, dataset_version, scene_id):
        pinned = self.members.get((dataset_id, dataset_version), [])
        return next((r for r in pinned if r.scene_id == scene_id), None)

    async def get_revision(self, *, scene_id, manifest_checksum):
        return next(
            (
                r
                for r in self.revisions
                if (r.scene_id, r.manifest_checksum) == (scene_id, manifest_checksum)
            ),
            None,
        )


class FakeSceneRunRepository:
    def __init__(self, runs: list | None = None) -> None:
        self.runs = list(runs or [])

    async def create(self, run):
        self.runs.append(run)
        return run

    async def get(self, run_id: str):
        return next((r for r in self.runs if r.run_id == run_id), None)

    async def update(self, run):
        return run

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scene_id: str | None = None,
        manifest_artifact_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        results = list(self.runs)
        if type is not None:
            results = [r for r in results if r.type == type]
        if status is not None:
            results = [r for r in results if r.status == status]
        if scene_id is not None:
            results = [r for r in results if r.scene_id == scene_id]
        if manifest_artifact_id is not None:
            results = [
                r for r in results if r.manifest_artifact_id == manifest_artifact_id
            ]
        # Newest-first, matching PostgresSceneRunRepository.list ordering.
        results = sorted(results, key=lambda r: r.created_at or 0, reverse=True)
        return results[offset : offset + limit]

    async def latest_succeeded_for_member_revisions(self, **kwargs):
        raise NotImplementedError


class FakeArtifactRepository:
    def __init__(self, artifacts: list[ArtifactRecord] | None = None) -> None:
        self.artifacts = list(artifacts or [])

    async def create(self, **kwargs) -> ArtifactRecord:
        raise NotImplementedError

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return next((a for a in self.artifacts if a.artifact_id == artifact_id), None)

    async def list(
        self,
        *,
        kind=None,
        owner_type=None,
        owner_id=None,
        dataset_id=None,
        dataset_version=None,
        scene_id: str | None = None,
        scenario_set_id=None,
        run_id=None,
        job_id=None,
        pipeline_run_id=None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArtifactRecord]:
        results = list(self.artifacts)
        if scene_id is not None:
            results = [a for a in results if a.scene_id == scene_id]
        return results[offset : offset + limit]


def sid(name: str, robot_run_id: str = "run-0") -> str:
    return unit_id_for(
        domain="scene", source_kind="recording", source_id=robot_run_id, unit_key=name
    )


def _scene(
    name: str, *, robot_run_id: str = "run-0", checksum: str = "sha256:" + "2" * 64
) -> SceneRevisionRecord:
    scene_id = sid(name, robot_run_id)
    return SceneRevisionRecord(
        scene_id=scene_id,
        source_kind="recording",
        robot_run_id=robot_run_id,
        unit_key=name,
        window_clock="mcap_log_time",
        window_start_timestamp_ns=0,
        window_end_timestamp_ns=20_000_000_000,
        producer_fingerprint="sha256:" + checksum[-1] * 64,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id, manifest_checksum=checksum
        ),
        manifest_checksum=checksum,
        observation_count=20,
        keyframe_count=10,
    )


def _service(
    scenes=None, runs=None, artifacts=None, members=None
) -> tuple[SceneService, FakeSceneRepository, FakeArtifactRepository]:
    repo = FakeSceneRepository(scenes, members)
    run_repo = FakeSceneRunRepository(runs)
    artifact_repo = FakeArtifactRepository(artifacts)
    return (
        SceneService(
            repository=repo, run_repository=run_repo, artifact_repository=artifact_repo
        ),
        repo,
        artifact_repo,
    )


# ── list_scenes ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_scenes_returns_all_when_unfiltered():
    service, _, _ = _service(scenes=[_scene("s1"), _scene("s2")])
    result = await service.list_scenes()
    assert result.count == 2
    assert {s.scene_id for s in result.scenes} == {sid("s1"), sid("s2")}
    assert all(isinstance(s, SceneRecord) for s in result.scenes)


@pytest.mark.asyncio
async def test_list_scenes_lists_units_not_revisions():
    service, _, _ = _service(
        scenes=[_scene("s1"), _scene("s1", checksum="sha256:" + "3" * 64)]
    )
    result = await service.list_scenes()
    assert [s.scene_id for s in result.scenes] == [sid("s1")]


@pytest.mark.asyncio
async def test_list_scenes_filters_by_robot_run():
    service, _, _ = _service(scenes=[_scene("s1"), _scene("s2", robot_run_id="run-1")])
    by_run = await service.list_scenes(robot_run_id="run-1")
    assert [s.scene_id for s in by_run.scenes] == [sid("s2", "run-1")]


@pytest.mark.asyncio
async def test_list_scenes_pagination():
    service, _, _ = _service(scenes=[_scene(f"s{i}") for i in range(5)])
    everything = await service.list_scenes()
    page = await service.list_scenes(limit=2, offset=2)
    assert [s.scene_id for s in page.scenes] == [
        s.scene_id for s in everything.scenes[2:4]
    ]


# ── get_scene ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_scene_returns_the_unit_and_every_revision():
    older = _scene("s1", checksum="sha256:" + "1" * 64)
    newer = _scene("s1")
    service, _, _ = _service(scenes=[older, newer])
    result = await service.get_scene(sid("s1"))
    assert result is not None
    assert result.scene.scene_id == sid("s1")
    assert [r.manifest_checksum for r in result.revisions] == [
        older.manifest_checksum,
        newer.manifest_checksum,
    ]


@pytest.mark.asyncio
async def test_get_scene_missing_returns_none():
    service, _, _ = _service(scenes=[])
    result = await service.get_scene("does-not-exist")
    assert result is None


# ── list_scene_artifacts ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_scene_artifacts_scoped_to_scene():
    artifacts = [
        ArtifactRecord(
            artifact_id="a1",
            kind="scene_manifest",
            uri="s3://x/a1.json",
            scene_id=sid("s1"),
        ),
        ArtifactRecord(
            artifact_id="a2",
            kind="scene_manifest",
            uri="s3://x/a2.json",
            scene_id=sid("s2"),
        ),
    ]
    service, _, _ = _service(scenes=[_scene("s1"), _scene("s2")], artifacts=artifacts)

    result = await service.list_scene_artifacts(sid("s1"))
    assert result is not None
    assert [a.artifact_id for a in result] == ["a1"]


@pytest.mark.asyncio
async def test_list_scene_artifacts_missing_scene_returns_none():
    service, _, _ = _service(scenes=[])
    result = await service.list_scene_artifacts("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_list_scene_artifacts_pagination():
    artifacts = [
        ArtifactRecord(
            artifact_id=f"a{i}",
            kind="scene_manifest",
            uri=f"s3://x/{i}.json",
            scene_id=sid("s1"),
        )
        for i in range(3)
    ]
    service, _, _ = _service(scenes=[_scene("s1")], artifacts=artifacts)

    page = await service.list_scene_artifacts(sid("s1"), limit=1, offset=1)
    assert [a.artifact_id for a in page] == ["a1"]


# ── get_scene_quality (lineage: correct run records feed the response) ───────


async def _quality(service, scene_id, dataset_id="nuscenes", version="v1"):
    return await service.get_scene_quality(
        scene_id, RevisionAddress(dataset_id=dataset_id, dataset_version=version)
    )


@pytest.mark.asyncio
async def test_get_scene_quality_missing_scene_returns_none():
    service, _, _ = _service(scenes=[])
    assert await _quality(service, "does-not-exist") is None


@pytest.mark.asyncio
async def test_get_scene_quality_of_a_scene_outside_the_version_returns_none():
    service, _, _ = _service(scenes=[_scene("s1")])
    assert await _quality(service, sid("s1"), version="v2") is None


def _pinned(run_cls, run_id, scene, *, revision="pinned", **fields):
    other = "sha256:" + "1" * 64
    artifact_id, checksum = (
        (scene.manifest_artifact_id, scene.manifest_checksum)
        if revision == "pinned"
        else (
            manifest_artifact_id_for(unit_id=scene.scene_id, manifest_checksum=other),
            other,
        )
    )
    return run_cls(
        run_id=run_id,
        scene_id=scene.scene_id,
        manifest_artifact_id=artifact_id,
        manifest_checksum=checksum,
        status=RunStatus.SUCCEEDED,
        **fields,
    )


@pytest.mark.asyncio
async def test_get_scene_quality_uses_runs_of_the_pinned_revision():
    from datetime import UTC, datetime

    scene = _scene("s1")
    current_validation = _pinned(
        SceneValidationRunRecord,
        "val-1",
        scene,
        validation_status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # Newer, but for another revision: must not shadow the pinned one.
    stale_validation = _pinned(
        SceneValidationRunRecord,
        "val-2",
        scene,
        revision="previous",
        validation_status="failed",
        should_block_pipeline=True,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    profile_run = _pinned(
        SceneProfileRunRecord,
        "prof-1",
        scene,
        keyframe_count=10,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service, _, _ = _service(
        scenes=[scene], runs=[current_validation, stale_validation, profile_run]
    )

    result = await _quality(service, scene.scene_id)
    assert result is not None
    assert (result.dataset_id, result.dataset_version) == ("nuscenes", "v1")
    assert result.validation.run_id == "val-1"
    assert result.readiness == "ready"
    assert result.profile.run_id == "prof-1"
    assert result.profile.keyframe_count == 10


@pytest.mark.asyncio
async def test_get_scene_quality_without_pinned_revision_runs_is_unknown():
    scene = _scene("s1")
    stale = _pinned(
        SceneValidationRunRecord,
        "val-old",
        scene,
        revision="previous",
        validation_status="ready",
    )
    service, _, _ = _service(scenes=[scene], runs=[stale])

    result = await _quality(service, scene.scene_id)
    assert result.validation is None
    assert result.readiness == "unknown"


@pytest.mark.asyncio
async def test_get_scene_quality_by_revision_needs_no_dataset_version():
    """A revision is assessed once, whatever pins it: addressed by its
    checksum, the same runs count and no DatasetVersion is reported."""
    scene = _scene("s1")
    validation = _pinned(
        SceneValidationRunRecord, "val-1", scene, validation_status="ready"
    )
    service, _, _ = _service(scenes=[scene], runs=[validation], members={})

    result = await service.get_scene_quality(
        scene.scene_id, RevisionAddress(manifest_checksum=scene.manifest_checksum)
    )
    assert result is not None
    assert (result.dataset_id, result.dataset_version) == (None, None)
    assert result.manifest_checksum == scene.manifest_checksum
    assert result.validation.run_id == "val-1"
    assert result.readiness == "ready"

    unknown = RevisionAddress(manifest_checksum="sha256:" + "1" * 64)
    assert await service.get_scene_quality(scene.scene_id, unknown) is None
