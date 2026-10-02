"""Phase 6.6 mission-boundary decision (docs/workflows/robot-run-and-mcap.md
§6): ``mission_boundary`` segmentation must fail loudly, not silently
return zero Episodes, when Mission(s) exist but their window(s) never
overlap any frame/robot-state timestamp -- the exact failure mode a
Kafka-captured MCAP hits (synthetic replay-clock Mission timestamps vs.
real historical CAN-channel timestamps). ``whole_run`` remains
unaffected and is the supported escape hatch. The "no Missions at all"
fallback-to-whole_run behavior is unchanged.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcap.writer import Writer

from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.episodes.schemas import EpisodeSegmentationConfig
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_worker.episodes.artifacts import EpisodeArtifactWriteResult
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_episodes import (
    BuildEpisodesJobHandler,
    UnsupportedSegmentationError,
)


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


def _make_context() -> MagicMock:
    context = MagicMock()
    context.default_dataset_id = "d1"
    context.default_dataset_version = "v1"
    context.dataset_store.get_version = AsyncMock(
        return_value=DatasetVersionRecord(dataset_id="d1", version="v1")
    )
    context.episode_artifact_store.write_episode_manifest = AsyncMock(
        side_effect=lambda **kw: EpisodeArtifactWriteResult(
            uri=f"mem://episodes/{kw['episode_id']}.json",
            checksum="sha256:deadbeef",
            size_bytes=123,
        )
    )
    context.artifact_record_store.create = AsyncMock()
    context.commit = AsyncMock()
    return context


# Simulates the real Kafka-capture timestamp mismatch: Mission at
# "replay-clock" time (~2026), CAN-derived frame at real 2018 CAN time --
# two completely disjoint epochs, matching Phase 6.3's frozen mapping.
_MISSION_LOG_TIME = 1_700_000_000_000_000_000  # ~2023, replay-session time
_CAN_FRAME_LOG_TIME = 1_500_000_000_000_000_000  # ~2018, real CAN time


async def test_mission_boundary_raises_when_missions_never_overlap_frames(
    tmp_path,
    register_local_recording,
) -> None:
    bag_path = str(tmp_path / "run.mcap")
    _write_mcap(
        bag_path,
        [
            (
                "/mission/status",
                _MISSION_LOG_TIME,
                {"mission_id": "mission-1", "operation_state": "running"},
                "json",
            ),
            (
                "/mission/status",
                _MISSION_LOG_TIME + 1_000_000_000,
                {"mission_id": "mission-1", "operation_state": "completed"},
                "json",
            ),
            (
                "/vehicle/odom",
                _CAN_FRAME_LOG_TIME,
                {"position": [1.0, 2.0, 0.0]},
                "json",
            ),
        ],
    )

    context = _make_context()
    with open(bag_path, "rb") as f:
        await register_local_recording(context, f.read())
    job = JobManifest(
        job_id="job-2", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1",
        dataset_version="v1",
        robot_run_id="run-1",
        segmentation=EpisodeSegmentationConfig(strategy="mission_boundary"),
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    with pytest.raises(UnsupportedSegmentationError, match="mission_boundary"):
        await BuildEpisodesJobHandler().run(request)

    # Nothing was written -- the guard fires after EpisodeBuilder.build()
    # but before any manifest/artifact write.
    context.episode_artifact_store.write_episode_manifest.assert_not_called()
    context.artifact_record_store.create.assert_not_called()
    context.commit.assert_not_called()


async def test_whole_run_succeeds_on_the_same_disjoint_timestamp_data(
    tmp_path, register_local_recording
) -> None:
    """The documented escape hatch: whole_run needs no Mission/CAN
    timestamp alignment, so it succeeds on exactly the data that makes
    mission_boundary raise above."""
    bag_path = str(tmp_path / "run.mcap")
    _write_mcap(
        bag_path,
        [
            (
                "/mission/status",
                _MISSION_LOG_TIME,
                {"mission_id": "mission-1", "operation_state": "running"},
                "json",
            ),
            (
                "/vehicle/odom",
                _CAN_FRAME_LOG_TIME,
                {"position": [1.0, 2.0, 0.0]},
                "json",
            ),
        ],
    )

    context = _make_context()
    with open(bag_path, "rb") as f:
        await register_local_recording(context, f.read())
    job = JobManifest(
        job_id="job-3", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1",
        dataset_version="v1",
        robot_run_id="run-1",
        segmentation=EpisodeSegmentationConfig(strategy="whole_run"),
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    result = await BuildEpisodesJobHandler().run(request)

    assert result.episode_count == 1
    assert result.segmentation_strategy == "whole_run"


async def test_mission_boundary_still_falls_back_to_whole_run_with_no_missions_at_all(
    tmp_path,
    register_local_recording,
) -> None:
    """Unchanged, legitimate behavior: zero Missions (not "Missions that
    don't overlap") degrades to whole_run silently, exactly as before --
    the new guard only fires when Missions exist but never overlap."""
    bag_path = str(tmp_path / "run.mcap")
    _write_mcap(
        bag_path,
        [("/vehicle/odom", _CAN_FRAME_LOG_TIME, {"position": [1.0, 2.0, 0.0]}, "json")],
    )

    context = _make_context()
    with open(bag_path, "rb") as f:
        await register_local_recording(context, f.read())
    job = JobManifest(
        job_id="job-4", type=JobType.BUILD_EPISODES, status=JobStatus.RUNNING
    )
    params = BuildEpisodesJobParams(
        dataset_id="d1",
        dataset_version="v1",
        robot_run_id="run-1",
        segmentation=EpisodeSegmentationConfig(strategy="mission_boundary"),
    )
    request = JobHandlerRequest(job=job, params=params, context=context)

    result = await BuildEpisodesJobHandler().run(request)

    assert result.episode_count == 1
    assert result.segmentation_strategy == "mission_boundary"
