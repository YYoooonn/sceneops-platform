"""Domain-isolation tests for BuildEpisodesJobHandler (SceneOps V2 Request 12).

Runs the handler end-to-end against a real (tmp-written) MCAP file with a
mocked WorkerContext, and asserts it never touches anything Scene-owned:
no ObservationArtifactStore/dataset_artifact_store call, no
RawLogManifest/RawLogFrameIndex written, no SCENE-owned artifact registered.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcap.writer import Writer

from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_episodes import BuildEpisodesJobHandler


def _write_mcap(path: str, messages: list[tuple[str, int, dict, str]]) -> None:
    with open(path, "wb") as f:
        writer = Writer(f)
        writer.start()
        schema_id = writer.register_schema(
            name="sceneops_json", encoding="jsonschema", data=b"{}"
        )
        channel_ids: dict[str, int] = {}
        for topic, log_time, payload, encoding in messages:
            if topic not in channel_ids:
                channel_ids[topic] = writer.register_channel(
                    topic=topic, message_encoding=encoding, schema_id=schema_id
                )
            writer.add_message(
                channel_ids[topic],
                log_time=log_time,
                data=json.dumps(payload).encode(),
                publish_time=log_time,
            )
        writer.finish()


def _make_context() -> WorkerContext:
    context = MagicMock()
    context.default_dataset_id = "d1"
    context.default_dataset_version = "v1"
    context.dataset_store.get_version = AsyncMock(
        return_value=DatasetVersionRecord(dataset_id="d1", version="v1")
    )
    context.episode_artifact_store.write_episode_manifest = AsyncMock(
        side_effect=lambda **kw: f"mem://episodes/{kw['episode_id']}.json"
    )
    context.artifact_record_store.create = AsyncMock()
    context.dataset_store.update_episode_summary = AsyncMock()
    context.commit = AsyncMock()
    return context


class TestBuildEpisodesDomainIsolation:
    @pytest.mark.asyncio
    async def test_run_never_touches_scene_domain_collaborators(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/mission/status",
                    1_000_000_000,
                    {"mission_id": "mission-1", "operation_state": "running"},
                    "json",
                ),
                (
                    "/mission/status",
                    2_000_000_000,
                    {"mission_id": "mission-1", "operation_state": "completed"},
                    "json",
                ),
                (
                    "/vehicle/odom",
                    1_500_000_000,
                    {"position": [1.0, 2.0, 0.0]},
                    "json",
                ),
            ],
        )

        context = _make_context()
        job = JobManifest(
            job_id="job-1", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
        )
        params = BuildEpisodesJobParams(
            dataset_id="d1",
            dataset_version="v1",
            robot_id="robot-1",
            mcap_uri=bag_path,
        )
        request = JobHandlerRequest(job=job, params=params, context=context)

        result = await BuildEpisodesJobHandler().run(request)

        assert result.episode_count == 1
        assert result.episode_ids == ["d1-v1-episodes-mission-1"]

        # No Scene-domain artifact I/O of any kind.
        context.dataset_artifact_store.dataset_version_root_uri.assert_not_called()
        context.artifact_store.write_json.assert_not_called()

        # Only EPISODE-owned artifacts were registered.
        for call in context.artifact_record_store.create.await_args_list:
            assert call.kwargs["owner_type"] == ArtifactOwnerType.EPISODE

        context.dataset_store.update_episode_summary.assert_awaited_once()
        context.commit.assert_awaited_once()
