"""Tests for EpisodeManifestProfiler (SceneOps V2 Request 17).

Pure descriptive stats — no usability judgment (that's the validator's job).
Covers counts/channels, timestamps/duration, and mission/task/outcome
metadata pass-through.
"""

from __future__ import annotations

from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeObservationFrame,
    EpisodeOutcome,
)
from sceneops_worker.episodes.profiling import EpisodeManifestProfiler


def _manifest(**overrides) -> EpisodeManifest:
    defaults = dict(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(raw_log_id="rl1", mission_id="mission-1"),
        task="pick_and_place",
        outcome=EpisodeOutcome.SUCCESS,
        observation_frames=[
            EpisodeObservationFrame(timestamp_us=1_000_000, channel="CAM_FRONT"),
            EpisodeObservationFrame(timestamp_us=2_000_000, channel="state.position"),
        ],
        action_frames=[
            EpisodeActionFrame(timestamp_us=1_000_000, channel="steering", value=0.1),
        ],
        observation_channels=["CAM_FRONT", "state.position"],
        action_channels=["steering"],
        control_frequency_hz=10.0,
        start_timestamp_us=1_000_000,
        end_timestamp_us=2_000_000,
        frame_count=3,
    )
    defaults.update(overrides)
    return EpisodeManifest(**defaults)


class TestCountsAndChannels:
    def test_counts_match_manifest_frame_lists(self) -> None:
        result = EpisodeManifestProfiler().profile(manifest=_manifest())
        assert result.frame_count == 3
        assert result.observation_count == 2
        assert result.action_count == 1

    def test_channels_pass_through(self) -> None:
        result = EpisodeManifestProfiler().profile(manifest=_manifest())
        assert result.observation_channels == ["CAM_FRONT", "state.position"]
        assert result.action_channels == ["steering"]

    def test_control_frequency_passes_through_unmodified(self) -> None:
        result = EpisodeManifestProfiler().profile(manifest=_manifest())
        assert result.control_frequency_hz == 10.0


class TestTimestampsAndDuration:
    def test_duration_computed_from_start_and_end(self) -> None:
        result = EpisodeManifestProfiler().profile(
            manifest=_manifest(start_timestamp_us=1_000_000, end_timestamp_us=6_000_000)
        )
        assert result.start_timestamp_us == 1_000_000
        assert result.end_timestamp_us == 6_000_000
        assert result.duration_us == 5_000_000

    def test_missing_timestamps_produce_no_duration(self) -> None:
        result = EpisodeManifestProfiler().profile(
            manifest=_manifest(start_timestamp_us=None, end_timestamp_us=None)
        )
        assert result.duration_us is None

    def test_partial_timestamps_produce_no_duration(self) -> None:
        result = EpisodeManifestProfiler().profile(
            manifest=_manifest(start_timestamp_us=1_000_000, end_timestamp_us=None)
        )
        assert result.duration_us is None


class TestMetadataPassthrough:
    def test_task_outcome_and_mission_id_pass_through(self) -> None:
        result = EpisodeManifestProfiler().profile(manifest=_manifest())
        assert result.task == "pick_and_place"
        assert result.outcome == "success"
        assert result.mission_id == "mission-1"

    def test_no_mission_produces_none_mission_id(self) -> None:
        result = EpisodeManifestProfiler().profile(
            manifest=_manifest(lineage=EpisodeLineage(raw_log_id="rl1"))
        )
        assert result.mission_id is None

    def test_no_task_produces_none_task(self) -> None:
        result = EpisodeManifestProfiler().profile(manifest=_manifest(task=None))
        assert result.task is None
