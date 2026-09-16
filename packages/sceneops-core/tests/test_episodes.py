from __future__ import annotations

from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeObservationFrame,
    EpisodeOutcome,
    EpisodeRecord,
    EpisodeStatus,
)


def test_episode_record_defaults():
    record = EpisodeRecord(episode_id="ep-1")
    assert record.status == EpisodeStatus.CREATED
    assert record.outcome == EpisodeOutcome.UNKNOWN
    assert record.observation_channels == []
    assert record.action_channels == []
    assert record.dataset_id is None


def test_episode_record_json_round_trip():
    record = EpisodeRecord(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        raw_log_id="rl1",
        robot_id="r1",
        robot_run_id="rr1",
        mission_id="m1",
        status=EpisodeStatus.REGISTERED,
        task="pick_and_place",
        outcome=EpisodeOutcome.SUCCESS,
        observation_channels=["CAM_FRONT", "state.position"],
        action_channels=["steering", "throttle"],
        control_frequency_hz=10.0,
        frame_count=42,
    )
    dumped = record.model_dump(mode="json")
    restored = EpisodeRecord.model_validate(dumped)
    assert restored == record


def test_episode_manifest_json_round_trip():
    manifest = EpisodeManifest(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(
            raw_log_id="rl1", robot_id="r1", robot_run_id="rr1", mission_id="m1"
        ),
        task="pick_and_place",
        outcome=EpisodeOutcome.SUCCESS,
        observation_frames=[
            EpisodeObservationFrame(
                timestamp_us=1_000_000,
                channel="CAM_FRONT",
                modality="camera",
                uri="s3://bucket/frame.jpg",
            ),
            EpisodeObservationFrame(
                timestamp_us=1_000_000,
                channel="state.position",
                values=[1.0, 2.0, 0.0],
            ),
        ],
        action_frames=[
            EpisodeActionFrame(timestamp_us=1_000_000, channel="steering", value=0.1),
        ],
        observation_channels=["CAM_FRONT", "state.position"],
        action_channels=["steering"],
        control_frequency_hz=5.0,
        start_timestamp_us=1_000_000,
        end_timestamp_us=1_000_000,
        frame_count=3,
    )
    dumped = manifest.model_dump(mode="json")
    restored = EpisodeManifest.model_validate(dumped)
    assert restored == manifest


def test_episode_observation_frame_defaults_have_no_uri_or_values():
    frame = EpisodeObservationFrame(timestamp_us=1, channel="x")
    assert frame.modality is None
    assert frame.uri is None
    assert frame.values is None
