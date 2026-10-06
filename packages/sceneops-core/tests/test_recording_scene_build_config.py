"""RecordingSceneBuildConfig contract (ADR-007 §29.10, §30)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneops_core.provenance import normalize_build_config
from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig


def _channel(topic, modality="lidar", clock="sensor.stamp", **extra):
    return {
        "topic": topic,
        "modality": modality,
        "time": {"source": "header_stamp", "clock": clock},
        "payload": "ros2_message",
        **extra,
    }


def _config(**overrides):
    config = {
        "channels": [_channel("/lidar/b"), _channel("/lidar/a")],
        "frames": {"ego_frame_id": "base_link"},
        "segmentation": {
            "policy": "fixed_duration",
            "clock": "sensor.stamp",
            "duration_ns": 1_000,
        },
    }
    config.update(overrides)
    return config


def test_normalized_form_is_explicit_sorted_and_plain_json():
    normalized = RecordingSceneBuildConfig.model_validate(_config()).normalized()
    assert [c["topic"] for c in normalized["channels"]] == ["/lidar/a", "/lidar/b"]
    assert normalized["calibration"] == {"static_transform_topics": ["/tf_static"]}
    assert normalized["segmentation"]["policy"] == "fixed_duration"
    assert normalized["channels"][0]["sensor_id"] is None
    assert normalize_build_config(normalized) == normalized


def test_channel_order_does_not_change_the_normalized_config():
    a = RecordingSceneBuildConfig.model_validate(_config()).normalized()
    b = RecordingSceneBuildConfig.model_validate(
        _config(channels=list(reversed(_config()["channels"])))
    ).normalized()
    assert a == b


@pytest.mark.parametrize(
    "time",
    [
        {"source": "log_time", "clock": "sensor.stamp"},
        {"source": "publish_time", "clock": "mcap_log_time"},
        {"source": "header_stamp", "clock": "mcap_log_time"},
        {"source": "header_stamp", "clock": "Not A Clock"},
    ],
)
def test_time_policy_clock_must_match_its_source(time):
    channel = _channel("/lidar/a")
    channel["time"] = time
    with pytest.raises(ValidationError):
        RecordingSceneBuildConfig.model_validate(_config(channels=[channel]))


def test_source_clock_segmentation_requires_every_channel_on_that_clock():
    with pytest.raises(ValidationError, match="must take its time from it"):
        RecordingSceneBuildConfig.model_validate(
            _config(
                channels=[_channel("/lidar/a"), _channel("/cam", clock="other.clock")]
            )
        )
    # On the recording clock every message has a timestamp: any channel clock.
    RecordingSceneBuildConfig.model_validate(
        _config(
            channels=[_channel("/lidar/a"), _channel("/cam", clock="other.clock")],
            segmentation={
                "policy": "fixed_duration",
                "clock": "mcap_log_time",
                "duration_ns": 1,
            },
        )
    )


def test_camera_channels_need_intrinsics_and_compressed_extraction_needs_a_camera():
    with pytest.raises(ValidationError, match="camera_info_topic"):
        RecordingSceneBuildConfig.model_validate(
            _config(channels=[_channel("/cam", modality="camera")])
        )
    lidar = _channel("/lidar/a")
    lidar["payload"] = "compressed_image"
    with pytest.raises(ValidationError, match="non-camera"):
        RecordingSceneBuildConfig.model_validate(_config(channels=[lidar]))


@pytest.mark.parametrize(
    "overrides",
    [
        {"channels": [_channel("/x"), _channel("/x")]},
        {"channels": []},
        {
            "segmentation": {
                "policy": "fixed_duration",
                "clock": "sensor.stamp",
                "duration_ns": 0,
            }
        },
        {"frames": {"ego_frame_id": "base_link", "world_frame_id": "base_link"}},
        {"source_format": "nuscenes"},
        {"robot_run_id": "run-1"},
    ],
)
def test_invalid_or_non_semantic_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        RecordingSceneBuildConfig.model_validate(_config(**overrides))


# --- segmentation policies ---------------------------------------------------------


def test_whole_recording_is_an_explicit_policy_on_one_declared_clock():
    config = RecordingSceneBuildConfig.model_validate(
        _config(segmentation={"policy": "whole_recording", "clock": "sensor.stamp"})
    )
    assert config.normalized()["segmentation"] == {
        "policy": "whole_recording",
        "clock": "sensor.stamp",
    }


@pytest.mark.parametrize(
    "segmentation",
    [
        {"clock": "sensor.stamp", "duration_ns": 1_000},  # no policy
        {"clock": "sensor.stamp"},  # no policy
        {"policy": "whole_recording"},  # no clock
        {"policy": "whole_recording", "clock": "sensor.stamp", "duration_ns": 1_000},
        {"policy": "fixed_duration", "clock": "sensor.stamp"},  # no duration
        {"policy": "per_keyframe", "clock": "sensor.stamp"},
    ],
)
def test_a_segmentation_must_state_its_policy_and_only_that_policy_fields(segmentation):
    with pytest.raises(ValidationError):
        RecordingSceneBuildConfig.model_validate(_config(segmentation=segmentation))


def test_whole_recording_on_a_source_clock_needs_every_channel_on_that_clock():
    with pytest.raises(ValidationError, match="must take its time from it"):
        RecordingSceneBuildConfig.model_validate(
            _config(
                channels=[_channel("/lidar/a"), _channel("/cam", clock="other.clock")],
                segmentation={"policy": "whole_recording", "clock": "sensor.stamp"},
            )
        )
    RecordingSceneBuildConfig.model_validate(
        _config(
            channels=[_channel("/lidar/a"), _channel("/cam", clock="other.clock")],
            segmentation={"policy": "whole_recording", "clock": "mcap_log_time"},
        )
    )
