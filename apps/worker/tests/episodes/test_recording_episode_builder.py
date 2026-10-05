"""RecordingEpisodeBuilder over real ROS 2 CDR MCAP recordings (ADR-007 §31).

Canonical Episodes keep every recorded occurrence of the configured streams
at its own source time, asynchronous and unaligned; segmentation is
explicit, deterministic and clock-explicit; identity never depends on
physical file order.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from sceneops_core.episodes.recording_build import RecordingEpisodeBuildConfig
from sceneops_core.episodes.testing import semantic_episode_content
from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig
from sceneops_worker.episodes.recording_builder import (
    RECORDING_EPISODE_PRODUCER_ID,
    RecordingEpisodeBuildError,
    iter_planned_payloads,
    plan_recording_episodes,
)
from sceneops_worker.recordings.payloads import RecordingRevision
from sceneops_worker.scenes.recording_builder import plan_recording_scenes

sys.path.insert(0, str(Path(__file__).parent))
from episode_recording_fixture import (  # noqa: E402
    ACTION_TIMES,
    CLOCK,
    CONTROL,
    LOG_LAG_NS,
    MISSION,
    OBSERVATION_TIMES,
    ODOM,
    STATE_TIMES,
    S,
    T0,
    episode_build_config,
    episode_recording,
    json_string,
    with_odometry,
)

sys.path.insert(0, str(Path(__file__).parents[1] / "scenes"))
from recording_fixture import (  # noqa: E402
    CAMERA_TOPIC,
    build_config as scene_build_config,
    default_recording,
)

REVISION = RecordingRevision("run-ep", "sha256:" + "a" * 64, "mcap_log_time")


def _plan(path, config=None, revision=REVISION):
    return plan_recording_episodes(
        path,
        revision=revision,
        config=RecordingEpisodeBuildConfig.model_validate(
            config or episode_build_config()
        ),
    )


@pytest.fixture()
def recording(tmp_path):
    return episode_recording().write(tmp_path / "episode.mcap")


def _seconds(occurrences) -> list[int]:
    return [(o.timestamp_ns - T0) // S for o in occurrences]


# --- canonical Episode content ---------------------------------------------------


def test_event_markers_define_one_episode_with_a_source_clock_window(recording):
    plan = _plan(recording)
    assert [e.unit_key for e in plan.episodes] == ["task-mission-1-000"]
    source = plan.episodes[0].manifest.lineage.source
    assert source.source_clock == CLOCK
    # [running marker, completed marker + 1): the end marker belongs to it.
    assert (source.start_timestamp_ns, source.end_timestamp_ns) == (T0, T0 + 9 * S + 1)
    assert plan.producer.producer_id == RECORDING_EPISODE_PRODUCER_ID


def test_streams_stay_asynchronous_and_unaligned(recording):
    manifest = _plan(recording).episodes[0].manifest
    assert _seconds(manifest.observations) == list(OBSERVATION_TIMES)
    assert _seconds(manifest.states) == list(STATE_TIMES)
    assert _seconds(manifest.actions) == list(ACTION_TIMES)
    assert _seconds(manifest.events) == [0, 9]
    # Nothing is resampled, interpolated or forward-filled: occurrence counts
    # are recorded message counts and no state appears at an action time
    # it was not recorded at.
    assert 5 not in _seconds(manifest.states)


def test_duplicate_source_events_remain_duplicate_occurrences(recording):
    states = _plan(recording).episodes[0].manifest.states
    at_t2 = [o for o in states if o.timestamp_ns == T0 + 2 * S]
    assert len(at_t2) == 2
    assert at_t2[0].occurrence_id != at_t2[1].occurrence_id


def test_values_are_selected_by_configuration_and_kept_as_recorded(recording):
    manifest = _plan(recording).episodes[0].manifest
    assert manifest.states[0].values == {"vx": 0.1, "x": 1.0, "y": 2.0}
    action = manifest.actions[0].values
    assert action == {"brake": 0, "steering": 0.2, "throttle": 0.5}
    assert isinstance(action["brake"], int)
    assert manifest.events[0].values == {"mission_id": "mission-1", "state": "running"}
    stream = manifest.stream(CONTROL)
    assert stream.schema_name == "std_msgs/msg/String"
    assert [f.path for f in stream.fields] == ["brake", "steering", "throttle"]


def test_action_schema_is_configuration_not_domain_model(recording):
    """A robot with one action field is the same builder, another config."""
    manifest = (
        _plan(recording, episode_build_config(action_fields=("steering",)))
        .episodes[0]
        .manifest
    )
    assert manifest.actions[0].values == {"steering": 0.2}


def test_observation_payloads_are_canonical_artifacts(recording):
    plan = _plan(recording)
    manifest = plan.episodes[0].manifest
    assert all(o.payload is not None for o in manifest.observations)
    assert {o.payload.media_type for o in manifest.observations} == {"image/jpeg"}
    config = RecordingEpisodeBuildConfig.model_validate(episode_build_config())
    yielded = list(iter_planned_payloads(recording, plan, config))
    assert len(yielded) == len(plan.payloads) == 3


# --- segmentation and clocks -----------------------------------------------------


def test_fixed_duration_windows_are_half_open(recording):
    plan = _plan(
        recording,
        episode_build_config(
            segmentation={
                "policy": "fixed_duration",
                "clock": CLOCK,
                "duration_ns": 3 * S,
            }
        ),
    )
    windows = {
        e.unit_key: (
            (e.manifest.lineage.source.start_timestamp_ns - T0) // S,
            (e.manifest.lineage.source.end_timestamp_ns - T0) // S,
        )
        for e in plan.episodes
    }
    assert windows == {
        "segment-000000": (0, 3),
        "segment-000001": (3, 6),
        "segment-000002": (6, 9),
        "segment-000003": (9, 12),
    }
    # The observation exactly at t3 is in the second window, not the first.
    by_key = {e.unit_key: e.manifest for e in plan.episodes}
    assert _seconds(by_key["segment-000000"].observations) == [0]
    assert _seconds(by_key["segment-000001"].observations) == [3]


def test_whole_recording_is_one_episode(recording):
    plan = _plan(
        recording,
        episode_build_config(
            segmentation={"policy": "whole_recording", "clock": CLOCK}
        ),
    )
    assert [e.unit_key for e in plan.episodes] == ["recording"]
    source = plan.episodes[0].manifest.lineage.source
    assert (source.start_timestamp_ns, source.end_timestamp_ns) == (T0, T0 + 9 * S + 1)


def test_streams_keep_their_source_clock_under_log_time_segmentation(recording):
    plan = _plan(
        recording,
        episode_build_config(
            segmentation={"policy": "whole_recording", "clock": "mcap_log_time"}
        ),
    )
    manifest = plan.episodes[0].manifest
    source = manifest.lineage.source
    assert source.source_clock == "mcap_log_time"
    assert source.start_timestamp_ns == T0 + LOG_LAG_NS
    # Occurrences keep source-clock timestamps; never converted to log time.
    assert _seconds(manifest.actions) == list(ACTION_TIMES)
    assert {s.source_clock for s in manifest.streams} == {CLOCK}


def test_different_clocks_across_streams_are_declared_per_stream(recording):
    config = episode_build_config(
        segmentation={"policy": "whole_recording", "clock": "mcap_log_time"}
    )
    config["streams"][0]["time"] = {"source": "log_time", "clock": "mcap_log_time"}
    manifest = _plan(recording, config).episodes[0].manifest
    clocks = {s.topic: s.source_clock for s in manifest.streams}
    assert clocks[ODOM] == "mcap_log_time" and clocks[CONTROL] == CLOCK
    assert manifest.states[0].timestamp_ns == T0 + 1 * S + LOG_LAG_NS


def test_two_tasks_yield_two_episodes_with_stable_keys(tmp_path):
    path = episode_recording(
        extra_markers=[(3, "running", "mission-2"), (5, "completed", "mission-2")]
    ).write(tmp_path / "r.mcap")
    plan = _plan(path)
    assert [e.unit_key for e in plan.episodes] == [
        "task-mission-1-000",
        "task-mission-2-000",
    ]
    second = {e.unit_key: e.manifest for e in plan.episodes}["task-mission-2-000"]
    assert _seconds(second.observations) == [3]
    assert _seconds(second.actions) == [5]


# --- determinism and identity ------------------------------------------------------


def test_same_recording_same_config_is_byte_identical(recording):
    a, b = _plan(recording), _plan(recording)
    assert [e.manifest.to_canonical_bytes() for e in a.episodes] == [
        e.manifest.to_canonical_bytes() for e in b.episodes
    ]
    assert a.producer == b.producer and a.payloads == b.payloads


def test_changed_config_changes_the_fingerprint_not_payload_ids(recording):
    a = _plan(recording)
    b = _plan(recording, episode_build_config(action_fields=("steering",)))
    assert a.producer.producer_fingerprint != b.producer.producer_fingerprint
    assert a.payloads == b.payloads


def test_cross_topic_interleaving_does_not_change_the_episode(tmp_path):
    rec = episode_recording()
    original = rec.write(tmp_path / "a.mcap")
    order = list(range(len(rec.messages)))
    # Reverse the global write order of different topics while keeping each
    # topic's own order: rotate the message list by topic.
    by_topic: dict[str, list[int]] = {}
    for index, message in enumerate(rec.messages):
        by_topic.setdefault(message.topic, []).append(index)
    order = [i for topic in sorted(by_topic, reverse=True) for i in by_topic[topic]]
    reordered = rec.write(tmp_path / "b.mcap", order=order, chunk_size=64)
    assert [e.manifest.to_canonical_bytes() for e in _plan(original).episodes] == [
        e.manifest.to_canonical_bytes() for e in _plan(reordered).episodes
    ]


def test_equivalent_acquisitions_share_semantic_content(tmp_path):
    rec = episode_recording()
    replay = copy.deepcopy(rec)
    for index, message in enumerate(replay.messages):
        message.log_time += 7 * S + index
        message.publish_time = message.log_time
    a = _plan(rec.write(tmp_path / "a.mcap"))
    b = _plan(
        replay.write(tmp_path / "b.mcap"),
        revision=RecordingRevision("run-replay", "sha256:" + "b" * 64, "mcap_log_time"),
    )
    assert [semantic_episode_content(e.manifest) for e in a.episodes] == [
        semantic_episode_content(e.manifest) for e in b.episodes
    ]


# --- loud failures -------------------------------------------------------------------


def test_configured_topic_absent_from_the_recording_fails(recording):
    config = episode_build_config()
    config["streams"][0]["topic"] = "/vehicle/missing"
    with pytest.raises(RecordingEpisodeBuildError, match="not in the recording"):
        _plan(recording, config)


def test_unresolvable_field_path_fails(recording):
    config = episode_build_config()
    config["streams"][0]["fields"][0]["path"] = "pose.pose.nothing"
    with pytest.raises(RecordingEpisodeBuildError, match="no field"):
        _plan(recording, config)


def test_a_field_resolving_to_a_structure_fails(recording):
    config = episode_build_config()
    config["streams"][0]["fields"][0]["path"] = "pose.pose.position"
    with pytest.raises(RecordingEpisodeBuildError, match="structure"):
        _plan(recording, config)


def test_json_string_decoding_needs_a_string_message(recording):
    config = episode_build_config()
    config["streams"][0]["decoding"] = "json_string"
    with pytest.raises(RecordingEpisodeBuildError, match="json_string"):
        _plan(recording, config)


@pytest.mark.parametrize(
    "markers, message",
    [
        ([(4, "completed", "mission-2")], "ends without having started"),
        ([(4, "running", "mission-2")], "never end"),
        ([(4, "running", "mission-1")], "starts again"),
    ],
)
def test_unpaired_task_markers_fail(tmp_path, markers, message):
    path = episode_recording(extra_markers=markers).write(tmp_path / "r.mcap")
    with pytest.raises(RecordingEpisodeBuildError, match=message):
        _plan(path)


def test_a_recording_without_markers_yields_no_episode_and_fails(tmp_path):
    path = episode_recording(
        mission=False, extra_markers=[(4, "paused", "mission-1")]
    ).write(tmp_path / "r.mcap")
    with pytest.raises(RecordingEpisodeBuildError, match="no Episode"):
        _plan(path)


def test_an_absent_marker_topic_fails(tmp_path):
    path = episode_recording(mission=False).write(tmp_path / "r.mcap")
    with pytest.raises(RecordingEpisodeBuildError, match="not in the recording"):
        _plan(path)


def test_log_time_needs_a_log_time_recording_clock(recording):
    config = episode_build_config(
        segmentation={"policy": "whole_recording", "clock": "mcap_log_time"}
    )
    with pytest.raises(RecordingEpisodeBuildError, match="recording clock"):
        _plan(
            recording,
            config,
            RecordingRevision("run-ep", "sha256:" + "a" * 64, "other"),
        )


def test_marker_values_must_be_strings(tmp_path):
    rec = episode_recording()
    rec.add(
        MISSION,
        "std_msgs/msg/String",
        json_string(
            mission_id=7, operation_state="running", source_timestamp_ns=T0 + 4 * S
        ),
        T0 + 4 * S,
        sequence=9,
    )
    with pytest.raises(RecordingEpisodeBuildError, match="must be strings"):
        _plan(rec.write(tmp_path / "r.mcap"))


def _scene_and_episode_recording(tmp_path):
    """The Scene fixture recording (camera + CameraInfo, lidar, /tf_static,
    /tf) plus odometry on the same source clock: one RobotRun both
    builders read."""
    rec = with_odometry(
        default_recording(), (1_000_000_000, 1_700_000_000, 2_400_000_000)
    )
    return rec.write(tmp_path / "both.mcap")


def _episode_config_for_scene_fixture() -> dict:
    header = {"source": "header_stamp", "clock": "sensor.header_stamp"}
    return {
        "streams": [
            {
                "topic": CAMERA_TOPIC,
                "role": "observation",
                "time": header,
                "payload": "compressed_image",
            },
            {
                "topic": ODOM,
                "role": "state",
                "time": header,
                "fields": [{"name": "x", "path": "pose.pose.position.x"}],
            },
        ],
        "segmentation": {"policy": "whole_recording", "clock": "sensor.header_stamp"},
    }


def _plan_scenes(path):
    return plan_recording_scenes(
        path,
        revision=REVISION,
        config=RecordingSceneBuildConfig.model_validate(scene_build_config()),
    )


def test_scene_and_episode_builders_are_independent_siblings(tmp_path):
    """Neither builder reads the other's output: from one RobotRun both run
    in either order with identical results, and the camera messages both
    extract are the same RobotRun-owned payload artifacts."""
    path = _scene_and_episode_recording(tmp_path)
    config = _episode_config_for_scene_fixture()

    episodes_first = _plan(path, config)
    scenes_second = _plan_scenes(path)
    scenes_first = _plan_scenes(path)
    episodes_second = _plan(path, config)

    assert [e.manifest.to_canonical_bytes() for e in episodes_first.episodes] == [
        e.manifest.to_canonical_bytes() for e in episodes_second.episodes
    ]
    assert [s.manifest.to_canonical_bytes() for s in scenes_first.scenes] == [
        s.manifest.to_canonical_bytes() for s in scenes_second.scenes
    ]
    episode_camera = {
        key: p for key, p in episodes_first.payloads.items() if key[0] == CAMERA_TOPIC
    }
    scene_camera = {
        key: p for key, p in scenes_first.payloads.items() if key[0] == CAMERA_TOPIC
    }
    assert episode_camera and episode_camera == scene_camera


def test_fixture_recordings_are_l1_conformant(tmp_path):
    from sceneops_integrations.recording import check_l1_recording

    assert check_l1_recording(episode_recording().write(tmp_path / "a.mcap")).conforms
    assert check_l1_recording(_scene_and_episode_recording(tmp_path)).conforms
