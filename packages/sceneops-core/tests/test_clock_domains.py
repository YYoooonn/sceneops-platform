"""Clock domains (ADR-015 §6.2, OQ-9, MS-5): the ClockDomain schema, the
manifest rules that bind every timestamped structure to a declared clock,
the origin of recording clocks, and comparability by origin instance."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    UnsupportedEpisodeManifestVersionError,
    load_canonical_episode_manifest,
)
from sceneops_core.episodes.testing import episode_manifest, state
from sceneops_core.provenance import (
    ClockDomain,
    ClockInstance,
    ClockOrigin,
    ContentUnitSource,
    IncomparableClocksError,
    clock_instance,
    recording_clock_domains,
    require_comparable,
)
from sceneops_core.scenes.schemas import (
    SceneManifest,
    UnsupportedSceneManifestVersionError,
    load_canonical_scene_manifest,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources.content import content_id_for

CONTENT_CHECKSUM = "sha256:" + "c" * 64


def _scene_payload(**kwargs) -> dict:
    return json.loads(build_scene_manifest(**kwargs).to_canonical_bytes())


def _episode_payload(**kwargs) -> dict:
    manifest = episode_manifest(
        [state("/odom", 1_000), state("/odom", 2_000)], **kwargs
    )
    return json.loads(manifest.to_canonical_bytes())


def _content_source(unit_key: str) -> ContentUnitSource:
    return ContentUnitSource(
        content_id=content_id_for(CONTENT_CHECKSUM),
        content_checksum=CONTENT_CHECKSUM,
        source_clock="format.frame_time",
        start_timestamp_ns=0,
        end_timestamp_ns=1,
        unit_key=unit_key,
    )


# --- the ClockDomain schema --------------------------------------------------------


def test_clock_domain_is_strict():
    domain = ClockDomain(
        clock="nuscenes.timestamp", origin="unix_epoch", resolution_ns=1_000
    )
    assert domain.origin == ClockOrigin.UNIX_EPOCH
    assert {o.value for o in ClockOrigin} == {"unix_epoch", "source", "unit"}
    for bad in (
        {"origin": "epoch"},
        {"resolution_ns": 0},
        {"resolution_ns": True},
        {"resolution_ns": 1.0},
        {"clock": "Nuscenes.Timestamp"},
        {"fps": 10},
    ):
        fields = {"clock": "a.clock", "origin": "source", "resolution_ns": 1, **bad}
        with pytest.raises(ValidationError):
            ClockDomain.model_validate(fields)


def test_recording_clock_domains_are_source_origin_ns_and_canonically_ordered():
    domains = recording_clock_domains(
        ["sensor.header_stamp", "mcap_log_time", "sensor.header_stamp"]
    )
    assert [(d.clock, d.origin, d.resolution_ns) for d in domains] == [
        ("mcap_log_time", ClockOrigin.SOURCE, 1),
        ("sensor.header_stamp", ClockOrigin.SOURCE, 1),
    ]


# --- SceneManifest v3 declarations ----------------------------------------------------


def test_scene_declares_the_window_clock_and_every_structure_clock():
    manifest = build_scene_manifest(
        source=recording_source(source_clock="mcap_log_time"),
        source_clock="lidar.header_stamp",
        camera_clock="camera.header_stamp",
    )
    assert [d.clock for d in manifest.clock_domains] == [
        "camera.header_stamp",
        "lidar.header_stamp",
        "mcap_log_time",
    ]
    data = manifest.to_canonical_bytes()
    assert load_canonical_scene_manifest(data) == manifest


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.pop("clock_domains"), "clock_domains"),
        (lambda p: p.update(clock_domains=[]), "clock_domains"),
        # The window's clock is used by the window alone here.
        (
            lambda p: p["clock_domains"].remove(
                next(d for d in p["clock_domains"] if d["clock"] == "mcap_log_time")
            ),
            "used but not declared",
        ),
        (
            lambda p: p["clock_domains"].append(
                {"clock": "other.clock", "origin": "source", "resolution_ns": 1}
            ),
            "declared but not used",
        ),
        (
            lambda p: p["clock_domains"].append(dict(p["clock_domains"][-1])),
            "declare each clock once",
        ),
        (
            lambda p: p["clock_domains"].append(
                {**p["clock_domains"][-1], "origin": "unix_epoch"}
            ),
            "declare each clock once",
        ),
        (lambda p: p["clock_domains"].reverse(), "sorted by clock"),
        (
            lambda p: p["clock_domains"][0].update(origin="unix_epoch"),
            "origin 'source'",
        ),
        (lambda p: p["clock_domains"][0].update(origin="unit"), "origin 'source'"),
        (
            lambda p: p["channels"][0].update(source_clock="undeclared.clock"),
            "used but not declared",
        ),
    ],
)
def test_scene_clock_declarations_are_strict(mutate, message):
    payload = _scene_payload(source_clock="lidar.header_stamp")
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        SceneManifest.model_validate(payload)


def test_scene_timestamps_lie_on_the_declared_resolution():
    payload = _scene_payload()
    # The default camera observations are 7 ns after their keyframes.
    payload["clock_domains"][0]["resolution_ns"] = 1_000
    with pytest.raises(ValidationError, match="not a multiple"):
        SceneManifest.model_validate(payload)


def test_previous_scene_manifest_version_is_rejected():
    payload = _scene_payload()
    payload.pop("clock_domains")
    payload["schema_version"] = "sceneops.scene_manifest/v2"
    with pytest.raises(UnsupportedSceneManifestVersionError):
        load_canonical_scene_manifest(json.dumps(payload).encode())


# --- EpisodeManifest v2 declarations --------------------------------------------------


def test_episode_declares_the_window_clock_and_every_stream_clock():
    manifest = episode_manifest(
        [state("/odom", 1_000), state("/cmd", 2_000, clock="mcap_log_time")],
        window=(0, 10_000),
        window_clock="mcap_log_time",
    )
    assert [(d.clock, d.origin) for d in manifest.clock_domains] == [
        ("mcap_log_time", ClockOrigin.SOURCE),
        ("sensor.header_stamp", ClockOrigin.SOURCE),
    ]
    assert load_canonical_episode_manifest(manifest.to_canonical_bytes()) == manifest


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update(clock_domains=[]), "clock_domains"),
        (
            lambda p: p["streams"][0].update(source_clock="undeclared.clock"),
            "used but not declared",
        ),
        (
            lambda p: p["clock_domains"].append(
                {"clock": "zz.unused", "origin": "source", "resolution_ns": 1}
            ),
            "declared but not used",
        ),
        (
            lambda p: p["clock_domains"][0].update(origin="unix_epoch"),
            "origin 'source'",
        ),
        (lambda p: p["clock_domains"][0].update(origin="unit"), "origin 'source'"),
        (
            lambda p: p["clock_domains"][0].update(resolution_ns=3),
            "not a multiple",
        ),
    ],
)
def test_episode_clock_declarations_are_strict(mutate, message):
    payload = _episode_payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        EpisodeManifest.model_validate(payload)


def test_previous_episode_manifest_version_is_rejected():
    payload = _episode_payload()
    payload.pop("clock_domains")
    payload["schema_version"] = "sceneops.episode_manifest/v1"
    with pytest.raises(UnsupportedEpisodeManifestVersionError):
        load_canonical_episode_manifest(json.dumps(payload).encode())


# --- comparability by origin instance ------------------------------------------------


def test_units_of_one_robot_run_share_a_source_clock_instance():
    a = build_scene_manifest(source=recording_source(unit_key="segment-0000"))
    b = build_scene_manifest(source=recording_source(unit_key="segment-0001"))
    assert a.clock_instance("mcap_log_time") == b.clock_instance("mcap_log_time")
    require_comparable(
        a.clock_instance("mcap_log_time"), b.clock_instance("mcap_log_time")
    )
    assert a.clock_instance("mcap_log_time") == ClockInstance(
        clock="mcap_log_time",
        origin=ClockOrigin.SOURCE,
        scope=("recording", "run-001"),
    )


def test_two_robot_runs_on_one_clock_id_are_not_comparable():
    a = build_scene_manifest(source=recording_source(robot_run_id="run-001"))
    b = build_scene_manifest(source=recording_source(robot_run_id="run-002"))
    with pytest.raises(IncomparableClocksError, match="not comparable"):
        require_comparable(
            a.clock_instance("mcap_log_time"), b.clock_instance("mcap_log_time")
        )
    episode_a = episode_manifest([state("/odom", 1)], robot_run_id="run-001")
    episode_b = episode_manifest([state("/odom", 1)], robot_run_id="run-002")
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            episode_a.clock_instance("sensor.header_stamp"),
            episode_b.clock_instance("sensor.header_stamp"),
        )


def test_two_clocks_of_one_robot_run_are_not_comparable():
    manifest = build_scene_manifest(camera_clock="camera.header_stamp")
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            manifest.clock_instance("mcap_log_time"),
            manifest.clock_instance("camera.header_stamp"),
        )


def test_unit_origin_is_comparable_only_within_its_unit():
    relative = ClockDomain(clock="format.frame_time", origin="unit", resolution_ns=1)
    first = clock_instance(relative, _content_source("0"))
    require_comparable(first, clock_instance(relative, _content_source("0")))
    with pytest.raises(IncomparableClocksError):
        require_comparable(first, clock_instance(relative, _content_source("1")))


def test_unix_epoch_is_comparable_across_sources():
    absolute = ClockDomain(
        clock="format.timestamp", origin="unix_epoch", resolution_ns=1
    )
    require_comparable(
        clock_instance(absolute, _content_source("0")),
        clock_instance(absolute, recording_source()),
    )
    source_origin = absolute.model_copy(update={"origin": ClockOrigin.SOURCE})
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            clock_instance(source_origin, _content_source("0")),
            clock_instance(source_origin, recording_source()),
        )
