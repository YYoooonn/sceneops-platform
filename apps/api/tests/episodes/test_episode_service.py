"""Unit tests for EpisodeService (SceneOps V2 Request 16).

Uses a tiny in-memory fake implementing the EpisodeRepository protocol,
matching this codebase's convention for API-layer service tests (see
apps/api/tests/platform/test_job_service.py) rather than a FastAPI
TestClient — no HTTP layer, just router-independent service behavior.

404/HTTP-contract behavior (missing Episode -> 404) is exercised live via
the pipeline-driven E2E instead, since this repo has no TestClient
precedent for any domain and the underlying raise_not_found() helper is
shared, already-proven infrastructure (see scenes/robots/datasets routers).
"""

from __future__ import annotations

import pytest

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeStatus
from sceneops_core.episodes.schemas.runs import (
    EpisodeProfileRunRecord,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType

from app.domains.episodes.schemas import EpisodeQualityReadiness
from app.domains.episodes.service import EpisodeService


class FakeEpisodeRepository:
    def __init__(self, episodes: list[EpisodeRecord] | None = None) -> None:
        self.episodes: dict[str, EpisodeRecord] = {
            e.episode_id: e for e in (episodes or [])
        }

    async def create(self, episode: EpisodeRecord) -> EpisodeRecord:
        self.episodes[episode.episode_id] = episode
        return episode

    async def upsert(self, episode: EpisodeRecord) -> EpisodeRecord:
        self.episodes[episode.episode_id] = episode
        return episode

    async def get(self, episode_id: str) -> EpisodeRecord | None:
        return self.episodes.get(episode_id)

    async def update(self, episode: EpisodeRecord) -> EpisodeRecord:
        self.episodes[episode.episode_id] = episode
        return episode

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: EpisodeStatus | None = None,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        mission_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]:
        results = list(self.episodes.values())
        if dataset_id is not None:
            results = [e for e in results if e.dataset_id == dataset_id]
        if dataset_version is not None:
            results = [e for e in results if e.dataset_version == dataset_version]
        if status is not None:
            results = [e for e in results if e.status == status]
        if robot_id is not None:
            results = [e for e in results if e.robot_id == robot_id]
        if robot_run_id is not None:
            results = [e for e in results if e.robot_run_id == robot_run_id]
        if mission_id is not None:
            results = [e for e in results if e.mission_id == mission_id]
        return results[offset : offset + limit]


def _episode(
    episode_id: str,
    *,
    dataset_id: str = "d1",
    dataset_version: str = "v1",
    robot_run_id: str | None = "run-1",
    mission_id: str | None = "m1",
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        robot_id="robot-1",
        robot_run_id=robot_run_id,
        mission_id=mission_id,
        status=EpisodeStatus.REGISTERED,
    )


class TestListEpisodes:
    @pytest.mark.asyncio
    async def test_returns_registered_episodes(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1"), _episode("ep-2")])
        service = EpisodeService(repository=repo)

        result = await service.list_episodes()

        assert result.count == 2
        assert {e.episode_id for e in result.episodes} == {"ep-1", "ep-2"}

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        service = EpisodeService(repository=FakeEpisodeRepository())

        result = await service.list_episodes()

        assert result.count == 0
        assert result.episodes == []

    @pytest.mark.asyncio
    async def test_filter_by_dataset_id(self) -> None:
        repo = FakeEpisodeRepository(
            [
                _episode("ep-1", dataset_id="d1"),
                _episode("ep-2", dataset_id="d2"),
            ]
        )
        service = EpisodeService(repository=repo)

        result = await service.list_episodes(dataset_id="d1")

        assert [e.episode_id for e in result.episodes] == ["ep-1"]

    @pytest.mark.asyncio
    async def test_filter_by_dataset_version(self) -> None:
        repo = FakeEpisodeRepository(
            [
                _episode("ep-1", dataset_version="v1"),
                _episode("ep-2", dataset_version="v2"),
            ]
        )
        service = EpisodeService(repository=repo)

        result = await service.list_episodes(dataset_id="d1", dataset_version="v2")

        assert [e.episode_id for e in result.episodes] == ["ep-2"]

    @pytest.mark.asyncio
    async def test_filter_by_robot_run_id(self) -> None:
        repo = FakeEpisodeRepository(
            [
                _episode("ep-1", robot_run_id="run-a"),
                _episode("ep-2", robot_run_id="run-b"),
            ]
        )
        service = EpisodeService(repository=repo)

        result = await service.list_episodes(robot_run_id="run-b")

        assert [e.episode_id for e in result.episodes] == ["ep-2"]

    @pytest.mark.asyncio
    async def test_filter_by_mission_id(self) -> None:
        repo = FakeEpisodeRepository(
            [
                _episode("ep-1", mission_id="mission-a"),
                _episode("ep-2", mission_id="mission-b"),
            ]
        )
        service = EpisodeService(repository=repo)

        result = await service.list_episodes(mission_id="mission-a")

        assert [e.episode_id for e in result.episodes] == ["ep-1"]

    @pytest.mark.asyncio
    async def test_pagination_limit_and_offset(self) -> None:
        repo = FakeEpisodeRepository([_episode(f"ep-{i}") for i in range(5)])
        service = EpisodeService(repository=repo)

        page = await service.list_episodes(limit=2, offset=2)

        assert page.count == 2
        assert [e.episode_id for e in page.episodes] == ["ep-2", "ep-3"]


class TestGetEpisode:
    @pytest.mark.asyncio
    async def test_existing_episode(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1")])
        service = EpisodeService(repository=repo)

        result = await service.get_episode("ep-1")

        assert result is not None
        assert result.episode.episode_id == "ep-1"

    @pytest.mark.asyncio
    async def test_missing_episode_returns_none(self) -> None:
        service = EpisodeService(repository=FakeEpisodeRepository())

        result = await service.get_episode("does-not-exist")

        assert result is None

    @pytest.mark.asyncio
    async def test_mission_boundary_episode_is_readable(self) -> None:
        """No API-level special-casing per segmentation strategy — an episode
        built via any strategy is just an EpisodeRecord row."""
        episode = _episode("ep-mission", mission_id="mission-scene-0061")
        repo = FakeEpisodeRepository([episode])
        service = EpisodeService(repository=repo)

        result = await service.get_episode("ep-mission")

        assert result is not None
        assert result.episode.mission_id == "mission-scene-0061"

    @pytest.mark.asyncio
    async def test_whole_run_or_fixed_window_episode_has_no_mission_id(self) -> None:
        """whole_run/fixed_window episodes have mission_id=None — the API
        must return them exactly as persisted, with no special handling."""
        episode = _episode("ep-fixed-window", mission_id=None)
        repo = FakeEpisodeRepository([episode])
        service = EpisodeService(repository=repo)

        result = await service.get_episode("ep-fixed-window")

        assert result is not None
        assert result.episode.mission_id is None


class FakeEpisodeRunRepository:
    """Append-only fake — .create() always adds a new row, matching the
    real run-record table's insert-only semantics. .list() returns rows
    newest-first, matching PostgresEpisodeRunRepository's created_at DESC
    ordering, so 'latest' is always runs[0]."""

    def __init__(self) -> None:
        self.runs: list[EpisodeValidationRunRecord | EpisodeProfileRunRecord] = []

    async def create(self, run):
        self.runs.insert(0, run)
        return run

    async def get(self, run_id: str):
        return next((r for r in self.runs if r.run_id == run_id), None)

    async def update(self, run):
        for i, existing in enumerate(self.runs):
            if existing.run_id == run.run_id:
                self.runs[i] = run
                return run
        raise ValueError(f"not found: {run.run_id}")

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        episode_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        results = self.runs
        if type is not None:
            results = [r for r in results if r.type == type]
        if episode_id is not None:
            results = [r for r in results if r.episode_id == episode_id]
        return results[offset : offset + limit]


def _validation_run(
    run_id: str, episode_id: str, *, validation_status: str, should_block: bool = False
) -> EpisodeValidationRunRecord:
    return EpisodeValidationRunRecord(
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
        episode_id=episode_id,
        validation_status=validation_status,
        should_block_pipeline=should_block,
    )


class TestGetEpisodeQuality:
    @pytest.mark.asyncio
    async def test_unknown_before_any_validation_run(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1")])
        service = EpisodeService(
            repository=repo, run_repository=FakeEpisodeRunRepository()
        )

        result = await service.get_episode_quality("ep-1")

        assert result is not None
        assert result.readiness == EpisodeQualityReadiness.UNKNOWN

    @pytest.mark.asyncio
    async def test_ready_for_valid_episode(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1")])
        run_repo = FakeEpisodeRunRepository()
        await run_repo.create(
            _validation_run("val-1", "ep-1", validation_status="ready")
        )
        service = EpisodeService(repository=repo, run_repository=run_repo)

        result = await service.get_episode_quality("ep-1")

        assert result is not None
        assert result.readiness == EpisodeQualityReadiness.READY

    @pytest.mark.asyncio
    async def test_blocked_for_blocking_issue(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1")])
        run_repo = FakeEpisodeRunRepository()
        await run_repo.create(
            _validation_run(
                "val-1", "ep-1", validation_status="failed", should_block=True
            )
        )
        service = EpisodeService(repository=repo, run_repository=run_repo)

        result = await service.get_episode_quality("ep-1")

        assert result is not None
        assert result.readiness == EpisodeQualityReadiness.BLOCKED

    @pytest.mark.asyncio
    async def test_missing_episode_returns_none(self) -> None:
        service = EpisodeService(
            repository=FakeEpisodeRepository(),
            run_repository=FakeEpisodeRunRepository(),
        )

        result = await service.get_episode_quality("does-not-exist")

        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_validation_runs_remain_queryable(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1")])
        run_repo = FakeEpisodeRunRepository()
        await run_repo.create(
            _validation_run(
                "val-1", "ep-1", validation_status="failed", should_block=True
            )
        )
        await run_repo.create(
            _validation_run("val-2", "ep-1", validation_status="ready")
        )
        service = EpisodeService(repository=repo, run_repository=run_repo)

        # Both runs are still in the append-only history...
        all_runs = await run_repo.list(
            type=RunType.EPISODE_VALIDATION, episode_id="ep-1"
        )
        assert len(all_runs) == 2

        # ...but quality reflects only the latest one.
        result = await service.get_episode_quality("ep-1")
        assert result is not None
        assert result.readiness == EpisodeQualityReadiness.READY
        assert result.validation.run_id == "val-2"

    @pytest.mark.asyncio
    async def test_multiple_profile_runs_remain_queryable_latest_wins(self) -> None:
        repo = FakeEpisodeRepository([_episode("ep-1")])
        run_repo = FakeEpisodeRunRepository()
        await run_repo.create(
            EpisodeProfileRunRecord(
                run_id="profile-1",
                status=RunStatus.SUCCEEDED,
                episode_id="ep-1",
                frame_count=5,
            )
        )
        await run_repo.create(
            EpisodeProfileRunRecord(
                run_id="profile-2",
                status=RunStatus.SUCCEEDED,
                episode_id="ep-1",
                frame_count=99,
            )
        )
        service = EpisodeService(repository=repo, run_repository=run_repo)

        all_runs = await run_repo.list(type=RunType.EPISODE_PROFILE, episode_id="ep-1")
        assert len(all_runs) == 2

        result = await service.get_episode_quality("ep-1")
        assert result is not None
        assert result.profile.run_id == "profile-2"
        assert result.profile.frame_count == 99
