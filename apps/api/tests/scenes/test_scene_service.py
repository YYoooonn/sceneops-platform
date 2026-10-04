"""Unit tests for SceneService (Stabilization Request 4 — closing the Scene
API coverage gap the audit found: only quality was previously tested).

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
from sceneops_core.scenes.schemas import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.scenes.service import SceneService


class FakeSceneRepository:
    def __init__(self, scenes: list[SceneRecord] | None = None) -> None:
        self.scenes: dict[str, SceneRecord] = {s.scene_id: s for s in (scenes or [])}

    async def get(self, scene_id: str) -> SceneRecord | None:
        return self.scenes.get(scene_id)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        results = list(self.scenes.values())
        if dataset_id is not None:
            results = [s for s in results if s.dataset_id == dataset_id]
        if dataset_version is not None:
            results = [s for s in results if s.dataset_version == dataset_version]
        if robot_run_id is not None:
            results = [s for s in results if s.robot_run_id == robot_run_id]
        return results[offset : offset + limit]


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
        dataset_id: str | None = None,
        dataset_version: str | None = None,
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

    async def latest_succeeded_for_current_revisions(self, **kwargs):
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


def _scene(scene_id: str, **overrides) -> SceneRecord:
    defaults = dict(
        scene_id=scene_id,
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        robot_run_id="run-0",
        unit_key=scene_id,
        window_clock="mcap_log_time",
        window_start_timestamp_ns=0,
        window_end_timestamp_ns=20_000_000_000,
        producer_fingerprint="sha256:" + "a" * 64,
        manifest_artifact_id=f"art-{scene_id}-rev-2",
        manifest_checksum="sha256:" + "2" * 64,
        observation_count=20,
        keyframe_count=10,
        annotation_count=0,
    )
    defaults.update(overrides)
    return SceneRecord(**defaults)


def _service(
    scenes=None, runs=None, artifacts=None
) -> tuple[SceneService, FakeSceneRepository, FakeArtifactRepository]:
    repo = FakeSceneRepository(scenes)
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
    assert {s.scene_id for s in result.scenes} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_list_scenes_filters_by_dataset_version():
    service, _, _ = _service(
        scenes=[
            _scene("s1", dataset_version="v1.0-mini"),
            _scene("s2", dataset_version="v2"),
        ]
    )
    result = await service.list_scenes(dataset_version="v2")
    assert result.count == 1
    assert result.scenes[0].scene_id == "s2"


@pytest.mark.asyncio
async def test_list_scenes_filters_by_robot_run():
    service, _, _ = _service(scenes=[_scene("s1"), _scene("s2", robot_run_id="run-1")])
    by_run = await service.list_scenes(robot_run_id="run-1")
    assert [s.scene_id for s in by_run.scenes] == ["s2"]


@pytest.mark.asyncio
async def test_list_scenes_pagination():
    service, _, _ = _service(scenes=[_scene(f"s{i}") for i in range(5)])
    page = await service.list_scenes(limit=2, offset=2)
    assert [s.scene_id for s in page.scenes] == ["s2", "s3"]


# ── get_scene ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_scene_returns_existing_detail():
    service, _, _ = _service(scenes=[_scene("s1")])
    result = await service.get_scene("s1")
    assert result is not None
    assert result.scene.scene_id == "s1"


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
            artifact_id="a1", kind="scene_manifest", uri="s3://x/a1.json", scene_id="s1"
        ),
        ArtifactRecord(
            artifact_id="a2", kind="scene_manifest", uri="s3://x/a2.json", scene_id="s2"
        ),
    ]
    service, _, _ = _service(scenes=[_scene("s1"), _scene("s2")], artifacts=artifacts)

    result = await service.list_scene_artifacts("s1")
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
            scene_id="s1",
        )
        for i in range(3)
    ]
    service, _, _ = _service(scenes=[_scene("s1")], artifacts=artifacts)

    page = await service.list_scene_artifacts("s1", limit=1, offset=1)
    assert [a.artifact_id for a in page] == ["a1"]


# ── get_scene_quality (lineage: correct run records feed the response) ───────


@pytest.mark.asyncio
async def test_get_scene_quality_missing_scene_returns_none():
    service, _, _ = _service(scenes=[])
    result = await service.get_scene_quality("does-not-exist")
    assert result is None


def _pinned(run_cls, run_id, scene, *, revision="current", **fields):
    artifact_id, checksum = (
        (scene.manifest_artifact_id, scene.manifest_checksum)
        if revision == "current"
        else (f"art-{scene.scene_id}-rev-1", "sha256:" + "1" * 64)
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
async def test_get_scene_quality_uses_runs_of_the_current_revision():
    from datetime import UTC, datetime

    scene = _scene("s1")
    current_validation = _pinned(
        SceneValidationRunRecord,
        "val-1",
        scene,
        validation_status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # Newer, but for the superseded revision: must not shadow the current one.
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

    result = await service.get_scene_quality("s1")
    assert result is not None
    assert result.validation.run_id == "val-1"
    assert result.readiness == "ready"
    assert result.profile.run_id == "prof-1"
    assert result.profile.keyframe_count == 10


@pytest.mark.asyncio
async def test_get_scene_quality_without_current_revision_runs_is_unknown():
    scene = _scene("s1")
    stale = _pinned(
        SceneValidationRunRecord,
        "val-old",
        scene,
        revision="previous",
        validation_status="ready",
    )
    service, _, _ = _service(scenes=[scene], runs=[stale])

    result = await service.get_scene_quality("s1")
    assert result.validation is None
    assert result.readiness == "unknown"
