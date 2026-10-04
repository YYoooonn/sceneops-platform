"""RecordingEpisodeBuildConfig: the producer's whole semantic configuration
(ADR-007 §29.10, §31.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneops_core.episodes.recording_build import RecordingEpisodeBuildConfig
from sceneops_core.provenance import (
    RecordingSourceRevision,
    compute_producer_fingerprint,
)

CLOCK = "vehicle.source_time"


def _config(**overrides) -> dict:
    config = {
        "streams": [
            {
                "topic": "/vehicle/odom",
                "role": "state",
                "time": {"source": "header_stamp", "clock": CLOCK},
                "fields": [
                    {"name": "x", "path": "pose.pose.position.x"},
                    {"name": "vx", "path": "twist.twist.linear.x"},
                ],
            },
            {
                "topic": "/vehicle/control",
                "role": "action",
                "decoding": "json_string",
                "time": {
                    "source": "payload_field",
                    "clock": CLOCK,
                    "field": "source_timestamp_ns",
                },
                "fields": [{"name": "steering", "path": "steering"}],
            },
        ],
        "events": [
            {
                "topic": "/mission/status",
                "decoding": "json_string",
                "time": {
                    "source": "payload_field",
                    "clock": CLOCK,
                    "field": "source_timestamp_ns",
                },
                "fields": [
                    {"name": "mission_id", "path": "mission_id"},
                    {"name": "state", "path": "operation_state"},
                ],
            }
        ],
        "segmentation": {
            "policy": "event_markers",
            "event_topic": "/mission/status",
            "key_field": "mission_id",
            "state_field": "state",
            "start_values": ["running"],
            "end_values": ["completed", "failed"],
        },
    }
    config.update(overrides)
    return config


def _fingerprint(config: RecordingEpisodeBuildConfig) -> str:
    return compute_producer_fingerprint(
        producer_id="sceneops.recording_episode_builder",
        semantics_version=1,
        build_config=config.normalized(),
        source=RecordingSourceRevision(
            robot_run_id="run-1", recording_checksum="sha256:" + "a" * 64
        ),
    )


def test_valid_config_names_topics_and_fields_verbatim() -> None:
    config = RecordingEpisodeBuildConfig.model_validate(_config())
    assert config.segmentation_clock() == CLOCK
    assert config.streams[1].fields[0].path == "steering"


def test_normalization_makes_unordered_collections_order_independent() -> None:
    a = RecordingEpisodeBuildConfig.model_validate(_config())
    raw = _config()
    raw["streams"] = list(reversed(raw["streams"]))
    raw["streams"][1]["fields"] = list(reversed(raw["streams"][1]["fields"]))
    raw["segmentation"]["end_values"] = ["failed", "completed"]
    b = RecordingEpisodeBuildConfig.model_validate(raw)
    assert a.normalized() == b.normalized()
    assert _fingerprint(a) == _fingerprint(b)


def test_defaults_are_explicit_in_the_normalized_form() -> None:
    normalized = RecordingEpisodeBuildConfig.model_validate(_config()).normalized()
    odom = next(s for s in normalized["streams"] if s["topic"] == "/vehicle/odom")
    assert odom["decoding"] == "ros2"
    assert odom["payload"] is None


def test_a_changed_semantic_choice_changes_the_fingerprint() -> None:
    a = RecordingEpisodeBuildConfig.model_validate(_config())
    raw = _config()
    raw["streams"][1]["role"] = "state"
    assert _fingerprint(a) != _fingerprint(
        RecordingEpisodeBuildConfig.model_validate(raw)
    )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda c: c["streams"].append(dict(c["streams"][0])), "more than one"),
        (
            lambda c: c["streams"][0]["time"].update(clock="other.clock"),
            "segmentation clock",
        ),
        (
            lambda c: c["segmentation"].update(event_topic="/not/configured"),
            "not a configured event source",
        ),
        (lambda c: c["segmentation"].update(key_field="nope"), "selects no field"),
        (
            lambda c: c["segmentation"].update(end_values=["running"]),
            "both start and end",
        ),
        (lambda c: c["streams"][0].update(fields=[]), "neither fields nor a payload"),
        (
            lambda c: c["streams"][0].update(payload="ros2_message"),
            "only an observation stream",
        ),
        (
            lambda c: c["streams"][0]["time"].update(source="log_time"),
            "is in clock",
        ),
        (
            lambda c: c["streams"][0]["time"].update(field="stamp"),
            "only for, payload_field",
        ),
        (lambda c: c["streams"][0]["fields"][0].update(path="a..b"), "field path"),
    ],
)
def test_invalid_configs_fail_loudly(mutate, message) -> None:
    raw = _config()
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        RecordingEpisodeBuildConfig.model_validate(raw)


def test_recording_clock_segmentation_admits_streams_on_other_clocks() -> None:
    raw = _config(segmentation={"policy": "whole_recording", "clock": "mcap_log_time"})
    raw["streams"][0]["time"] = {"source": "log_time", "clock": "mcap_log_time"}
    config = RecordingEpisodeBuildConfig.model_validate(raw)
    assert config.segmentation_clock() == "mcap_log_time"


def test_execution_context_is_not_build_config() -> None:
    raw = _config()
    raw["job_id"] = "job-1"
    with pytest.raises(ValidationError):
        RecordingEpisodeBuildConfig.model_validate(raw)
