"""Tests for ValidateEpisodeJobHandler (SceneOps V2 Request 17).

Runs the handler end-to-end against a mocked WorkerContext, asserting:
run-record persistence (job-level + per-episode), report artifact
registration under EPISODE_VALIDATION_RUN (not Scene-owned), and that no
Scene-domain collaborator is ever touched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.episodes.schemas import (
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
    ValidateEpisodeJobParams,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.validate_episode import ValidateEpisodeJobHandler


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
        frame_count=1,
    )


def _episode_manifest(episode_id: str) -> EpisodeManifest:
    return EpisodeManifest(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(
            raw_log_id="rl1", robot_id="robot-1", robot_run_id="run-1", mission_id="m1"
        ),
        outcome=EpisodeOutcome.SUCCESS,
        observation_frames=[
            EpisodeObservationFrame(timestamp_us=1_000_000, channel="state.position")
        ],
        observation_channels=["state.position"],
        action_channels=["steering"],
        frame_count=1,
    )


def _make_context(episodes: dict[str, EpisodeRecord], manifests: dict) -> MagicMock:
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
        job_id="job-validate-1",
        type=JobType.VALIDATE_EPISODE,
        status=JobStatus.RUNNING,
        pipeline_run_id="pipe-1",
    )
    params = ValidateEpisodeJobParams(
        dataset_id="d1", dataset_version="v1", episode_ids=episode_ids
    )
    return JobHandlerRequest(job=job, params=params, context=context)


class TestValidateEpisodeHandler:
    @pytest.mark.asyncio
    async def test_valid_episode_is_ready_and_does_not_block(self) -> None:
        episodes = {"ep-1": _episode_record("ep-1")}
        manifests = {"ep-1": _episode_manifest("ep-1")}
        context = _make_context(episodes, manifests)

        result = await ValidateEpisodeJobHandler().run(_request(["ep-1"], context))

        assert result.status == "ready"
        assert result.should_block_pipeline is False
        assert result.checked_episode_count == 1
        assert result.issue_count == 0

        # RunRecordHandler upserts the job-level record twice (RUNNING, then
        # SUCCEEDED) plus one per-episode run record upserted inside execute().
        assert context.runs.episode_runs.upsert.await_count == 3

        # Only EPISODE_VALIDATION_RUN-owned artifacts were registered.
        for call in context.artifact_record_store.create.await_args_list:
            assert call.kwargs["owner_type"] == ArtifactOwnerType.EPISODE_VALIDATION_RUN

        # No Scene-domain collaborator touched.
        context.scene_store.get.assert_not_called()
        context.scene_artifact_store.load_scene_manifest.assert_not_called()
        context.dataset_artifact_store.dataset_version_root_uri.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_episode_blocks_pipeline(self) -> None:
        empty_manifest = _episode_manifest("ep-empty").model_copy(
            update={
                "frame_count": 0,
                "observation_frames": [],
                "observation_channels": [],
            }
        )
        episodes = {
            "ep-empty": _episode_record("ep-empty").model_copy(
                update={"frame_count": 0}
            )
        }
        manifests = {"ep-empty": empty_manifest}
        context = _make_context(episodes, manifests)

        result = await ValidateEpisodeJobHandler().run(_request(["ep-empty"], context))

        assert result.status == "failed"
        assert result.should_block_pipeline is True
        assert result.issue_count >= 1

    @pytest.mark.asyncio
    async def test_missing_episode_record_is_blocking(self) -> None:
        context = _make_context(episodes={}, manifests={})

        result = await ValidateEpisodeJobHandler().run(
            _request(["does-not-exist"], context)
        )

        assert result.should_block_pipeline is True
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_no_episode_ids_fails_fast(self) -> None:
        context = _make_context(episodes={}, manifests={})

        result = await ValidateEpisodeJobHandler().run(_request([], context))

        assert result.should_block_pipeline is True
        assert result.checked_episode_count == 0
