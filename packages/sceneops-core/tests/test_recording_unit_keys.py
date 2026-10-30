"""Recording unit keys carry the segmentation (ADR-015 §6.3).

    unit_key = <policy>-<sha256(canonical_json({schema, **boundary}))[:16]>/<segment>

Expected keys are recomputed here from the documented formula with
``hashlib`` and ``json`` directly, never taken from the function under test.
The boundary document holds exactly what places unit boundaries: changing it
yields other units, changing anything else (channels, streams, fields that
are not the boundary) keeps the units and is a new revision.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from sceneops_core.episodes.recording_build import RecordingEpisodeBuildConfig
from sceneops_core.provenance import canonical_unit_id, unit_id_for
from sceneops_core.provenance.segmentation import (
    fixed_duration_index,
    fixed_duration_window,
    recording_unit_key,
)
from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig
from sceneops_core.scenes.testing import recording_source

CLOCK = "sensor.stamp"


def _expected_key(boundary: dict, segment: str) -> str:
    document = {"recording_segmentation_schema": "sceneops.recording_segmentation/v1"}
    document.update(boundary)
    data = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return f"{boundary['policy']}-{hashlib.sha256(data).hexdigest()[:16]}/{segment}"


def _channel(topic: str) -> dict:
    return {
        "topic": topic,
        "modality": "lidar",
        "time": {"source": "header_stamp", "clock": CLOCK},
        "payload": "ros2_message",
    }


_TEN_US = {"policy": "fixed_duration", "clock": CLOCK, "duration_ns": 10_000}


def _scene_config(
    *, channels=("/lidar/a",), segmentation=_TEN_US
) -> RecordingSceneBuildConfig:
    return RecordingSceneBuildConfig.model_validate(
        {
            "channels": [_channel(t) for t in channels],
            "frames": {"ego_frame_id": "base_link"},
            "segmentation": segmentation,
        }
    )


_MARKER_SOURCE = {
    "topic": "/mission/status",
    "decoding": "json_string",
    "time": {"source": "payload_field", "clock": CLOCK, "field": "t_ns"},
    "fields": [
        {"name": "mission_id", "path": "mission_id"},
        {"name": "state", "path": "operation_state"},
    ],
}
_MARKER_SEGMENTATION = {
    "policy": "event_markers",
    "event_topic": "/mission/status",
    "key_field": "mission_id",
    "state_field": "state",
    "start_values": ["running"],
    "end_values": ["failed", "completed"],
}


def _episode_config(*, events=None, segmentation=None, streams=None):
    return RecordingEpisodeBuildConfig.model_validate(
        {
            "streams": streams
            or [
                {
                    "topic": "/odom",
                    "role": "state",
                    "time": {"source": "header_stamp", "clock": CLOCK},
                    "fields": [{"name": "x", "path": "pose.position.x"}],
                }
            ],
            "events": events if events is not None else [_MARKER_SOURCE],
            "segmentation": segmentation or _MARKER_SEGMENTATION,
        }
    )


# --- the key formula --------------------------------------------------------------


def test_the_key_is_the_documented_formula():
    boundary = {"policy": "fixed_duration", "clock": CLOCK, "duration_ns": 10_000}
    assert recording_unit_key(boundary=boundary, segment="3") == _expected_key(
        boundary, "3"
    )


def test_the_key_is_canonical_over_the_boundary_document():
    a = {"policy": "fixed_duration", "clock": CLOCK, "duration_ns": 5}
    b = {"duration_ns": 5, "clock": CLOCK, "policy": "fixed_duration"}
    assert recording_unit_key(boundary=a, segment="0") == recording_unit_key(
        boundary=b, segment="0"
    )


@pytest.mark.parametrize(
    "boundary",
    [{}, {"policy": "Fixed Duration"}, {"policy": ""}],
)
def test_a_boundary_document_names_a_canonical_policy(boundary):
    with pytest.raises(ValueError, match="segmentation policy"):
        recording_unit_key(boundary=boundary, segment="0")


@pytest.mark.parametrize("segment", ["", " 1", "a\nb"])
def test_a_segment_is_a_verbatim_key(segment):
    with pytest.raises(ValueError, match="segment key"):
        recording_unit_key(
            boundary={"policy": "whole_recording", "clock": CLOCK}, segment=segment
        )


# --- fixed-duration grid ------------------------------------------------------------


def test_fixed_duration_windows_are_a_grid_anchored_at_the_clock_zero():
    assert fixed_duration_index(0, 10) == 0
    assert fixed_duration_index(9, 10) == 0
    assert fixed_duration_index(10, 10) == 1
    assert fixed_duration_window(3, 10) == (30, 40)
    for t in (0, 1, 9_999, 10_000, 1_760_000_000_123_456_789):
        start, end = fixed_duration_window(fixed_duration_index(t, 7), 7)
        assert start <= t < end


@pytest.mark.parametrize("duration", [0, -1])
def test_fixed_duration_needs_a_positive_duration(duration):
    with pytest.raises(ValueError):
        fixed_duration_index(1, duration)
    with pytest.raises(ValueError):
        fixed_duration_window(1, duration)


# --- what is and is not boundary --------------------------------------------------


def test_scene_boundary_is_the_segmentation_only():
    config = _scene_config()
    assert config.segmentation_boundary() == {
        "policy": "fixed_duration",
        "clock": CLOCK,
        "duration_ns": 10_000,
    }
    # Another channel selection: the same units (a new revision of each).
    other = _scene_config(channels=("/lidar/a", "/lidar/b"))
    assert other.segmentation_boundary() == config.segmentation_boundary()


@pytest.mark.parametrize(
    "segmentation",
    [
        {**_TEN_US, "duration_ns": 5_000},
        {**_TEN_US, "clock": "mcap_log_time"},
        {"policy": "whole_recording", "clock": CLOCK},
    ],
)
def test_another_scene_segmentation_is_other_units(segmentation):
    base = _scene_config().segmentation_boundary()
    other = _scene_config(segmentation=segmentation).segmentation_boundary()
    assert recording_unit_key(boundary=base, segment="0") != recording_unit_key(
        boundary=other, segment="0"
    )


def test_two_segmentations_of_one_run_give_distinct_units():
    ten = _scene_config().segmentation_boundary()
    five = _scene_config(
        segmentation={**_TEN_US, "duration_ns": 5_000}
    ).segmentation_boundary()

    def unit(boundary):
        source = recording_source(
            robot_run_id="run-1",
            unit_key=recording_unit_key(boundary=boundary, segment="0"),
        )
        return canonical_unit_id(domain="scene", source=source)

    assert unit(ten) != unit(five)


def test_event_marker_boundary_reads_the_marker_paths_not_their_names():
    boundary = _episode_config().segmentation_boundary()
    assert boundary == {
        "policy": "event_markers",
        "event_topic": "/mission/status",
        "time": {"source": "payload_field", "clock": CLOCK, "field": "t_ns"},
        "decoding": "json_string",
        "key_path": "mission_id",
        "state_path": "operation_state",
        "start_values": ["running"],
        "end_values": ["completed", "failed"],
    }

    # Renaming the fields (same paths) and selecting one more marker field
    # change nothing about where Episodes start and end.
    renamed = copy.deepcopy(_MARKER_SOURCE)
    renamed["fields"] = [
        {"name": "task", "path": "mission_id"},
        {"name": "phase", "path": "operation_state"},
        {"name": "note", "path": "note"},
    ]
    segmentation = {**_MARKER_SEGMENTATION, "key_field": "task", "state_field": "phase"}
    assert (
        _episode_config(
            events=[renamed], segmentation=segmentation
        ).segmentation_boundary()
        == boundary
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda source, seg: source["fields"][0].update(path="task_id"),
        lambda source, seg: source["time"].update(field="stamp_ns"),
        lambda source, seg: source.update(decoding="ros2"),
        lambda source, seg: seg.update(start_values=["started"]),
        lambda source, seg: seg.update(end_values=["completed"]),
    ],
)
def test_another_marker_rule_is_other_units(change):
    source = copy.deepcopy(_MARKER_SOURCE)
    segmentation = copy.deepcopy(_MARKER_SEGMENTATION)
    change(source, segmentation)
    other = _episode_config(events=[source], segmentation=segmentation)
    assert other.segmentation_boundary() != _episode_config().segmentation_boundary()


def test_episode_streams_are_not_boundary():
    more = [
        {
            "topic": "/odom",
            "role": "state",
            "time": {"source": "header_stamp", "clock": CLOCK},
            "fields": [
                {"name": "x", "path": "pose.position.x"},
                {"name": "y", "path": "pose.position.y"},
            ],
        }
    ]
    assert (
        _episode_config(streams=more).segmentation_boundary()
        == _episode_config().segmentation_boundary()
    )


def test_unit_id_for_is_the_unit_id_of_a_binding():
    source = recording_source(robot_run_id="run-9", unit_key="whole_recording-x/a")
    assert canonical_unit_id(domain="episode", source=source) == unit_id_for(
        domain="episode",
        source_kind="recording",
        source_id="run-9",
        unit_key="whole_recording-x/a",
    )
    with pytest.raises(ValueError, match="source_kind"):
        unit_id_for(domain="scene", source_kind="other", source_id="r", unit_key="k")
