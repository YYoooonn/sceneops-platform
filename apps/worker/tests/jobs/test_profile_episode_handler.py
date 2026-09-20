"""Tests for ProfileEpisodeJobHandler (SceneOps V2 Request 17).

Descriptive-only — never fails/blocks. Confirms per-episode + job-level
report artifacts are EPISODE_PROFILE_RUN-owned and no Scene-domain
collaborator is touched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeObservationFrame,
    EpisodeOutcome,
    EpisodeRecord,
    EpisodeStatus,
)
from sceneops_core.jobs.schemas import (
    JobManifest,
    JobStatus,
    JobType,
    ProfileEpisodeJobParams,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.profile_episode import ProfileEpisodeJobHandler


def _episode_record(episode_id: str) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        robot_id="robot-1",
        robot_run_id="run-1",
        mission_id="m1",
        status=EpisodeStatus.REGISTERED,
        episode_manifest_uri=f"mem://episodes/{episode_id}.json",
        frame_count=2,
    )


def _episode_manifest(episode_id: str) -> EpisodeManifest:
    return EpisodeManifest(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(raw_log_id="rl1", mission_id="m1"),
        task="pick",
        outcome=EpisodeOutcome.SUCCESS,
        observation_frames=[
            EpisodeObservationFrame(timestamp_us=1_000_000, channel="state.position")
        ],
        action_frames=[
            EpisodeActionFrame(timestamp_us=1_000_000, channel="steering", value=0.1)
        ],
        observation_channels=["state.position"],
        action_channels=["steering"],
        start_timestamp_us=1_000_000,
        end_timestamp_us=2_000_000,
        frame_count=2,
    )


def _make_context(episodes: dict, manifests: dict) -> MagicMock:
    context = MagicMock()
    context.episode_store.get = AsyncMock(side_effect=lambda eid: episodes.get(eid))
    context.episode_artifact_store.load_episode_manifest = AsyncMock(
        side_effect=lambda uri: next(
            (m for eid, m in manifests.items() if uri.endswith(f"{eid}.json")), None
        )
    )
    context.artifact_store.join_uri = MagicMock(
        side_effect=lambda *parts: "/".join(parts)
    )
    context.artifact_store.write_json = AsyncMock()
    context.artifact_record_store.create = AsyncMock()
    context.runs.episode_runs.upsert = AsyncMock(side_effect=lambda r: r)
    context.settings.run_root_uri = "mem://runs"
    context.commit = AsyncMock()
    context.rollback = AsyncMock()
    return context


def _request(episode_ids: list[str], context: MagicMock) -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-profile-1",
        type=JobType.PROFILE_EPISODE,
        status=JobStatus.RUNNING,
        pipeline_run_id="pipe-1",
    )
    params = ProfileEpisodeJobParams(
        dataset_id="d1", dataset_version="v1", episode_ids=episode_ids
    )
    return JobHandlerRequest(job=job, params=params, context=context)


class TestProfileEpisodeHandler:
    @pytest.mark.asyncio
    async def test_profile_reports_expected_counts_and_channels(self) -> None:
        episodes = {"ep-1": _episode_record("ep-1")}
        manifests = {"ep-1": _episode_manifest("ep-1")}
        context = _make_context(episodes, manifests)

        result = await ProfileEpisodeJobHandler().run(_request(["ep-1"], context))

        assert result.checked_episode_count == 1
        assert result.frame_count == 2
        assert result.observation_count == 1
        assert result.action_count == 1
        assert result.observed_observation_channels == ["state.position"]
        assert result.observed_action_channels == ["steering"]

        for call in context.artifact_record_store.create.await_args_list:
            assert call.kwargs["owner_type"] == ArtifactOwnerType.EPISODE_PROFILE_RUN

        context.scene_store.get.assert_not_called()
        context.scene_artifact_store.load_scene_manifest.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_episode_is_skipped_not_fatal(self) -> None:
        context = _make_context(episodes={}, manifests={})

        result = await ProfileEpisodeJobHandler().run(
            _request(["does-not-exist"], context)
        )

        assert result.checked_episode_count == 0

    @pytest.mark.asyncio
    async def test_no_episode_ids_raises(self) -> None:
        context = _make_context(episodes={}, manifests={})

        with pytest.raises(ValueError, match="episode_id"):
            await ProfileEpisodeJobHandler().run(_request([], context))
