"""EpisodeManifest v4: content bindings, frame timeline, component names,
transition streams, video-frame payloads and native references (ADR-015
§6.2, §6.5, §6.6; ADR-017; M3.0 D8, D9). Plus the frame-time rule they rest
on."""

from __future__ import annotations

import json
import struct
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeManifestError,
    UnsupportedEpisodeManifestVersionError,
    load_canonical_episode_manifest,
)
from sceneops_core.jobs.schemas import JobType, parse_job_params
from sceneops_core.provenance import (
    ContentUnitSource,
    ProducerInfo,
    frame_index_of,
    frame_time_ns,
)
from sceneops_core.sources import content_id_for

CHECKSUM = "sha256:" + "c" * 64
CONTENT_ID = content_id_for(CHECKSUM)
OTHER_CONTENT_ID = content_id_for("sha256:" + "d" * 64)
CLOCK = "lerobot.frame_time"
VIDEO = "videos/observation.image/chunk-000/file-000.mp4"
VIDEO_URI = format_logical_uri(
    LogicalNamespace.SOURCES, f"contents/{CONTENT_ID}/files/{VIDEO}"
)


# --- frame time ------------------------------------------------------------------


@pytest.mark.parametrize("fps", [10, 30, 50])
def test_frame_time_is_round_half_even_of_the_exact_rational(fps):
    for k in range(0, 5_000):
        t = frame_time_ns(k, fps)
        assert t == round(Fraction(k * 10**9, fps))
        assert frame_index_of(t, fps) == k
        assert frame_index_of(t + 1, fps) is None
        if t:
            assert frame_index_of(t - 1, fps) is None


def test_frame_time_ties_round_to_even():
    # 10⁹ / 16 = 62,500,000 exactly; 10⁹ · k / 2,000,000,000 has .5 ties.
    assert frame_time_ns(1, 2_000_000_000) == 0
    assert frame_time_ns(3, 2_000_000_000) == 2


@pytest.mark.parametrize("fps", [0, -10, 10.0, True])
def test_frame_time_needs_a_positive_integer_rate(fps):
    with pytest.raises(ValueError):
        frame_time_ns(1, fps)


def test_float32_values_round_trip_bit_exactly_through_canonical_json():
    values = [
        struct.unpack("<f", struct.pack("<f", x / 7))[0] for x in range(-500, 500)
    ]
    manifest = _manifest(states=[[v, -v] for v in values[:3]])
    reread = load_canonical_episode_manifest(manifest.to_canonical_bytes())
    for occurrence in reread.states:
        for value in occurrence.values["value"]:
            assert struct.pack("<f", value) == struct.pack("<f", float(value))
    blob = json.loads(
        _manifest(states=[[values[1], values[2]]] * 3).to_canonical_bytes()
    )
    assert blob["states"][0]["values"]["value"] == [values[1], values[2]]


# --- a content-sourced manifest ------------------------------------------------------


def _occurrence(topic: str, k: int, fps: int, values: dict[str, Any], **extra):
    return {
        "occurrence_id": f"{topic}-{k:08d}",
        "topic": topic,
        "timestamp_ns": frame_time_ns(k, fps),
        "values": values,
        "native_ref": str(100 + k),
        **extra,
    }


def _document(
    *,
    frames: int = 3,
    fps: int = 10,
    states: list[list[float]] | None = None,
    done: list[bool] | None = None,
    origin: str = "unit",
    component_names: list[str] | None = ("motor_0", "motor_1"),  # type: ignore[assignment]
) -> dict[str, Any]:
    states = states or [[float(k), -float(k)] for k in range(frames)]
    done = done if done is not None else [k == frames - 1 for k in range(frames)]
    source = ContentUnitSource(
        content_id=CONTENT_ID,
        content_checksum=CHECKSUM,
        source_clock=CLOCK,
        start_timestamp_ns=0,
        end_timestamp_ns=frame_time_ns(frames - 1, fps) + 1,
        unit_key="0",
    )
    producer = ProducerInfo.create(
        producer_id="sceneops.lerobot_episode_builder",
        semantics_version=1,
        build_config={"features": []},
        source=source.source_revision(),
    )
    payload = {
        "uri": VIDEO_URI,
        "checksum": "sha256:" + "e" * 64,
        "size_bytes": 1000,
        "media_type": "video/mp4",
    }
    return {
        "schema_version": "sceneops.episode_manifest/v4",
        "lineage": {
            "source": source.model_dump(mode="json"),
            "producer": producer.model_dump(mode="json"),
        },
        "clock_domains": [{"clock": CLOCK, "origin": origin, "resolution_ns": 1}],
        "frame_timeline": {"clock": CLOCK, "fps": fps, "frame_count": frames},
        "streams": [
            {
                "topic": "next.done",
                "role": "transition",
                "schema_name": "bool",
                "source_clock": CLOCK,
                "fields": [{"name": "value", "path": "next.done"}],
            },
            {
                "topic": "observation.image",
                "role": "observation",
                "schema_name": "video",
                "source_clock": CLOCK,
                "has_payload": True,
            },
            {
                "topic": "observation.state",
                "role": "state",
                "schema_name": "float32",
                "source_clock": CLOCK,
                "fields": [
                    {
                        "name": "value",
                        "path": "observation.state",
                        "component_names": (
                            list(component_names) if component_names else None
                        ),
                    }
                ],
            },
        ],
        "observations": [
            _occurrence(
                "observation.image",
                k,
                fps,
                {},
                payload={
                    **payload,
                    "selector": {
                        "kind": "video_frame",
                        "timestamp_ns": frame_time_ns(k, fps),
                    },
                },
            )
            for k in range(frames)
        ],
        "states": [
            _occurrence("observation.state", k, fps, {"value": states[k]})
            for k in range(frames)
        ],
        "transitions": [
            _occurrence("next.done", k, fps, {"value": done[k]}) for k in range(frames)
        ],
    }


def _manifest(**kwargs) -> EpisodeManifest:
    return EpisodeManifest.model_validate(_document(**kwargs))


def test_a_content_sourced_frame_grid_episode_is_canonical():
    manifest = _manifest()
    data = manifest.to_canonical_bytes()
    assert load_canonical_episode_manifest(data) == manifest
    assert manifest.lineage.source.source_kind == "content"
    assert manifest.frame_timeline.window() == (0, 200_000_001)
    assert [o.native_ref for o in manifest.states] == ["100", "101", "102"]
    assert manifest.observations[2].payload.selector.timestamp_ns == 200_000_000
    assert manifest.observations[2].payload.uri == VIDEO_URI
    instance = manifest.clock_instance(CLOCK)
    assert instance.scope == ("content", CONTENT_ID, "0")


@pytest.mark.parametrize(
    "version", ["sceneops.episode_manifest/v2", "sceneops.episode_manifest/v3"]
)
def test_earlier_versions_are_refused(version):
    document = _document()
    document["schema_version"] = version
    with pytest.raises(UnsupportedEpisodeManifestVersionError):
        load_canonical_episode_manifest(json.dumps(document).encode())


def _invalid(document: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        EpisodeManifest.model_validate(document)


def test_an_occurrence_off_the_frame_grid_is_refused():
    document = _document()
    document["states"][1]["timestamp_ns"] += 1
    _invalid(document, "not a frame of the timeline")


def test_an_occurrence_beyond_frame_count_is_refused():
    document = _document()
    document["frame_timeline"]["frame_count"] = 2
    _invalid(document, "window .* disagrees with the frame timeline")
    document = _document()
    document["states"].append(
        _occurrence("observation.state", 3, 10, {"value": [0.0, 0.0]})
    )
    # Frame 3 of a 3-frame timeline lies past the window, so the window refuses it.
    _invalid(document, "outside the episode window")


def test_a_window_that_disagrees_with_frame_count_is_refused():
    document = _document()
    document["lineage"]["source"]["end_timestamp_ns"] += 1
    _invalid(document, "disagrees with the frame timeline")


def test_a_frame_timeline_clock_must_have_unit_origin():
    _invalid(_document(origin="unix_epoch"), "must have origin 'unit'")
    _invalid(_document(origin="source"), "must have origin 'unit'")


def test_component_names_fix_every_value_length():
    _invalid(_document(states=[[1.0], [2.0], [3.0]]), "must be a list of 2 components")
    document = _document()
    document["states"][0]["values"]["value"] = 1.0
    _invalid(document, "must be a list of 2 components")
    # Without names, a vector of any length is a value.
    assert _manifest(states=[[1.0], [2.0, 3.0], []], component_names=None)


@pytest.mark.parametrize("fault", ["missing", "duplicated"])
def test_a_transition_stream_has_exactly_one_occurrence_per_frame(fault):
    document = _document()
    transitions = document["transitions"]
    if fault == "missing":
        del transitions[1]
    else:
        transitions[2] = {**transitions[1], "occurrence_id": "next.done-dup"}
        transitions.sort(
            key=lambda o: (o["topic"], o["timestamp_ns"], o["occurrence_id"])
        )
    _invalid(document, "exactly one occurrence per frame")


def test_transition_streams_need_a_frame_timeline():
    document = _document()
    document["frame_timeline"] = None
    _invalid(document, "need a frame_timeline")


def test_whose_bytes_a_uri_names_is_not_decided_by_the_schema():
    """A file of another content validates here; registration refuses it
    (sceneops_worker.payload_registration)."""
    document = _document()
    document["observations"][0]["payload"]["uri"] = VIDEO_URI.replace(
        CONTENT_ID, OTHER_CONTENT_ID
    )
    assert EpisodeManifest.model_validate(document)


def test_a_frame_selector_needs_a_video_payload():
    document = _document()
    document["observations"][0]["payload"]["media_type"] = "image/jpeg"
    _invalid(document, "cannot select part of")


def test_native_ref_is_a_verbatim_key():
    document = _document()
    document["states"][0]["native_ref"] = ""
    _invalid(document, "native_ref")


def test_loader_reports_invalid_documents():
    document = _document()
    document["frame_timeline"]["fps"] = 0
    with pytest.raises(EpisodeManifestError):
        load_canonical_episode_manifest(json.dumps(document).encode())


# --- LeRobot build configuration and snapshot build params ---------------------------


@pytest.mark.parametrize(
    "feature", ["timestamp", "frame_index", "episode_index", "index", "task_index"]
)
def test_structural_features_are_not_selectable(feature):
    with pytest.raises(ValidationError, match="structural"):
        LeRobotEpisodeBuildConfig(features=[{"feature": feature, "role": "state"}])


def test_config_roles_and_normalization():
    with pytest.raises(ValidationError):
        LeRobotEpisodeBuildConfig(features=[{"feature": "a", "role": "event"}])
    with pytest.raises(ValidationError, match="more than once"):
        LeRobotEpisodeBuildConfig(
            features=[
                {"feature": "a", "role": "state"},
                {"feature": "a", "role": "action"},
            ]
        )
    config = LeRobotEpisodeBuildConfig(
        features=[{"feature": "z", "role": "state"}, {"feature": "a", "role": "action"}]
    )
    assert [f["feature"] for f in config.normalized()["features"]] == ["a", "z"]
    assert config.normalized()["task_events"] is True


SNAPSHOT_ID = "snapshot-" + "a" * 32


def _snapshot_params(**overrides):
    return {
        "snapshot_id": SNAPSHOT_ID,
        "build_config": {"features": [{"feature": "action", "role": "action"}]},
        **overrides,
    }


def test_snapshot_build_params_keep_unit_keys_as_canonical_execution_scope():
    params = parse_job_params(
        JobType.BUILD_SNAPSHOT_EPISODES, _snapshot_params(unit_keys=["2", "10", "0"])
    )
    assert params.unit_keys == ["0", "10", "2"]
    assert (
        parse_job_params(JobType.BUILD_SNAPSHOT_EPISODES, _snapshot_params()).unit_keys
        is None
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit_keys": []},
        {"unit_keys": ["0", "0"]},
        {"snapshot_id": "content-" + "a" * 32},
        {"dataset_id": "ds"},
        {"datasetVersion": "v1"},
    ],
)
def test_snapshot_build_params_refuse_invalid_scope(overrides):
    with pytest.raises(ValidationError):
        parse_job_params(JobType.BUILD_SNAPSHOT_EPISODES, _snapshot_params(**overrides))


# --- alignment (D14) ----------------------------------------------------------------


def test_alignment_refuses_transition_streams_and_carries_frame_references():
    from sceneops_core.episodes.alignment import (
        ChannelNamespace,
        UnsupportedEpisodeContentError,
        alignment_samples,
    )

    with pytest.raises(UnsupportedEpisodeContentError, match="transition streams"):
        alignment_samples(_manifest(), CLOCK)
    document = _document()
    document["streams"] = [s for s in document["streams"] if s["role"] != "transition"]
    document["transitions"] = []
    document["frame_timeline"] = None
    samples = alignment_samples(EpisodeManifest.model_validate(document), CLOCK)
    frames = samples[ChannelNamespace.OBSERVATION]["observation.image"]
    assert [s.payload.reference_uri for s in frames] == [VIDEO_URI] * 3
    # The selector travels with the URI: without it the reference would name
    # the whole video, not the frame.
    assert [s.payload.reference_metadata["selector"] for s in frames] == [
        {"kind": "video_frame", "timestamp_ns": frame_time_ns(k, 10)} for k in range(3)
    ]
