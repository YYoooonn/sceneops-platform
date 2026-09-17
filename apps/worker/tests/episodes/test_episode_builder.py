"""Tests for EpisodeBuilder.

Covers synthetic Mission-boundary segmentation (obs/action field
classification, fallback when no missions exist) and a real-fixture test
against the same `can_replay_scene_0061.mcap` used by
`apps/worker/tests/datasets/test_rosbag_raw_log.py` — proving the builder
works against genuine ROS2 CDR-decoded robot state, not just synthetic data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock


from sceneops_core.episodes.schemas import EpisodeOutcome, EpisodeSource
from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.robots.schemas import MissionRecord, MissionStatus, RobotStateRecord
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.episodes.building import EpisodeBuilder

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"


def _state(timestamp_us: int, **fields) -> RobotStateRecord:
    return RobotStateRecord(
        state_id=f"s-{timestamp_us}",
        robot_id="r1",
        timestamp_us=timestamp_us,
        **fields,
    )


def _mission(
    mission_id: str, *, start_s: float, end_s: float | None, status
) -> MissionRecord:
    return MissionRecord(
        mission_id=mission_id,
        robot_id="r1",
        status=status,
        started_at=datetime.fromtimestamp(start_s, tz=timezone.utc),
        ended_at=datetime.fromtimestamp(end_s, tz=timezone.utc)
        if end_s is not None
        else None,
    )


class TestSyntheticSegmentation:
    def test_splits_by_mission_boundaries_and_classifies_fields(self) -> None:
        frames = [
            RawSensorFrameManifest(
                frame_id="f1",
                timestamp_us=1_000_000,
                channel="CAM_FRONT",
                modality="camera",
                uri="s3://x/1.jpg",
            ),
            RawSensorFrameManifest(
                frame_id="f2",
                timestamp_us=5_000_000,
                channel="CAM_FRONT",
                modality="camera",
                uri="s3://x/2.jpg",
            ),
        ]
        states = [
            _state(1_000_000, position=[0.0, 0.0, 0.0], steering=0.1),
            _state(2_000_000, position=[1.0, 0.0, 0.0], steering=0.2),
            _state(6_000_000, position=[5.0, 0.0, 0.0], steering=0.5),
        ]
        missions = [
            _mission("m1", start_s=1.0, end_s=3.0, status=MissionStatus.COMPLETED),
            _mission("m2", start_s=5.0, end_s=7.0, status=MissionStatus.FAILED),
        ]
        source = EpisodeSource(frames=frames, robot_states=states, missions=missions)

        result = EpisodeBuilder().build(
            dataset_id="d1",
            dataset_version="v1",
            raw_log_id="rl1",
            robot_id="r1",
            robot_run_id="rr1",
            source=source,
        )

        assert result.episode_count == 2
        ep1, ep2 = result.episodes
        assert ep1.episode_id == "rl1-m1"
        assert ep1.outcome == EpisodeOutcome.SUCCESS
        assert ep1.observation_channels == ["CAM_FRONT", "state.position"]
        assert ep1.action_channels == ["steering"]
        assert ep1.frame_count == 5  # 1 sensor + 2 state.position + 2 steering

        assert ep2.episode_id == "rl1-m2"
        assert ep2.outcome == EpisodeOutcome.FAILURE
        assert ep2.frame_count == 3  # 1 sensor + 1 state.position + 1 steering

        assert result.observation_frame_count == sum(
            len(e.observation_frames) for e in result.episodes
        )
        assert result.action_frame_count == sum(
            len(e.action_frames) for e in result.episodes
        )

    def test_no_missions_falls_back_to_single_episode(self) -> None:
        states = [_state(1_000_000, battery=90.0)]
        source = EpisodeSource(frames=[], robot_states=states, missions=[])

        result = EpisodeBuilder().build(
            dataset_id="d1",
            dataset_version="v1",
            raw_log_id="rl1",
            robot_id="r1",
            robot_run_id=None,
            source=source,
        )

        assert result.episode_count == 1
        episode = result.episodes[0]
        assert episode.episode_id == "rl1-episode0000"
        assert episode.lineage.mission_id is None
        assert episode.outcome == EpisodeOutcome.UNKNOWN
        assert episode.observation_channels == ["state.battery"]

    def test_empty_window_is_dropped(self) -> None:
        missions = [
            _mission("m1", start_s=1.0, end_s=2.0, status=MissionStatus.COMPLETED)
        ]
        source = EpisodeSource(frames=[], robot_states=[], missions=missions)

        result = EpisodeBuilder().build(
            dataset_id="d1",
            dataset_version="v1",
            raw_log_id="rl1",
            robot_id="r1",
            robot_run_id=None,
            source=source,
        )

        assert result.episode_count == 0
        assert result.episodes == []


class TestRealFixture:
    """Exercises the builder against the same real CAN-replay-recorded MCAP
    fixture used for RosbagAdapter's own tests."""

    def test_can_replay_bag_becomes_one_successful_episode(self) -> None:
        """No ObservationArtifactStore constructed at all — proves the
        Episode path no longer depends on the Scene-domain collaborator."""
        bag_path = str(_FIXTURES_DIR / "can_replay_scene_0061.mcap")
        adapter = RosbagAdapter(
            source_store=MagicMock(),
            source_root_uri=bag_path,
        )

        source = adapter.extract_episode_source(
            robot_id="robot-nuscenes-01", robot_run_id="run-scene-0061"
        )

        result = EpisodeBuilder().build(
            dataset_id="d1",
            dataset_version="v1",
            raw_log_id="rl-scene-0061",
            robot_id="robot-nuscenes-01",
            robot_run_id="run-scene-0061",
            source=source,
        )

        assert result.episode_count == 1
        episode = result.episodes[0]
        assert episode.episode_id == "rl-scene-0061-mission-scene-0061"
        assert episode.outcome == EpisodeOutcome.SUCCESS
        assert episode.lineage.mission_id == "mission-scene-0061"
        assert episode.lineage.robot_id == "robot-nuscenes-01"
        assert episode.lineage.robot_run_id == "run-scene-0061"
        assert set(episode.observation_channels) >= {
            "state.position",
            "state.velocity",
            "state.acceleration",
            "state.battery",
        }
        assert set(episode.action_channels) == {"steering", "throttle", "brake"}
        assert episode.control_frequency_hz is not None
        assert episode.control_frequency_hz > 0
