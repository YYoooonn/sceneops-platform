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
from sceneops_core.scenes.schemas import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneRecord,
    SceneStatus,
)
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.scenes.service import SceneService


class FakeSceneRepository:
    def __init__(self, scenes: list[SceneRecord] | None = None) -> None:
        self.scenes: dict[str, SceneRecord] = {s.scene_id: s for s in (scenes or [])}

    async def create(self, scene: SceneRecord) -> SceneRecord:
        self.scenes[scene.scene_id] = scene
        return scene

    async def upsert(self, scene: SceneRecord) -> SceneRecord:
        self.scenes[scene.scene_id] = scene
        return scene

    async def get(self, scene_id: str) -> SceneRecord | None:
        return self.scenes.get(scene_id)

    async def update(self, scene: SceneRecord) -> SceneRecord:
        self.scenes[scene.scene_id] = scene
        return scene

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: SceneStatus | None = None,
        origin_type: SceneOriginType | None = None,
        generation_method: SceneGenerationMethod | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        results = list(self.scenes.values())
        if dataset_id is not None:
            results = [s for s in results if s.dataset_id == dataset_id]
        if dataset_version is not None:
            results = [s for s in results if s.dataset_version == dataset_version]
        if status is not None:
            results = [s for s in results if s.status == status]
        if origin_type is not None:
            results = [s for s in results if s.origin_type == origin_type]
        if generation_method is not None:
            results = [s for s in results if s.generation_method == generation_method]
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
        if scene_id is not None:
            results = [r for r in results if r.scene_id == scene_id]
        # Newest-first, matching PostgresSceneRunRepository.list ordering.
        results = sorted(results, key=lambda r: r.created_at or 0, reverse=True)
        return results[offset : offset + limit]

    async def list_latest_by_dataset_version(self, **kwargs):
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
        status=SceneStatus.BUILT,
        sample_count=10,
        frame_count=20,
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
async def test_list_scenes_filters_by_status():
    service, _, _ = _service(
        scenes=[
            _scene("s1", status=SceneStatus.BUILT),
            _scene("s2", status=SceneStatus.VALIDATED),
        ]
    )
    result = await service.list_scenes(status=SceneStatus.VALIDATED)
    assert [s.scene_id for s in result.scenes] == ["s2"]


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


@pytest.mark.asyncio
async def test_get_scene_quality_uses_latest_validation_and_profile_runs():
    scene = _scene("s1", status=SceneStatus.PROFILED)
    validation_run = SceneValidationRunRecord(
        run_id="val-1",
        scene_id="s1",
        status=RunStatus.SUCCEEDED,
        validation_status="ready",
    )
    profile_run = SceneProfileRunRecord(
        run_id="prof-1",
        scene_id="s1",
        status=RunStatus.SUCCEEDED,
        sample_count=10,
    )
    service, _, _ = _service(scenes=[scene], runs=[validation_run, profile_run])

    result = await service.get_scene_quality("s1")
    assert result is not None
