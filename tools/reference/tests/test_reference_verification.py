"""The source-grounded reference verifier (tools/reference/verification).

Offline tests run on hand-built recordings (tiny_recording); the real-data tests
run on the locked corpus and raw nuScenes when they are present locally and skip
otherwise. No test reads or writes platform state.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tiny_recording as tiny  # noqa: E402
from verification import cdr, negative, observation, rules  # noqa: E402
from verification.contract import (  # noqa: E402
    REPO_ROOT,
    check_expectations,
    derive,
    expectations_document,
    load_sources,
)
from verification.expectations import (  # noqa: E402
    ExpectationError,
    expected_episodes,
    expected_scene,
)
from verification.findings import Findings  # noqa: E402
from verification.mcap_records import McapFormatError, iter_messages_from_bytes  # noqa: E402
from verification.recording import recording_facts_from_bytes  # noqa: E402

import reference_verify  # noqa: E402

SCENE_CONFIG = json.loads(
    (REPO_ROOT / "config/baselines/scene_build_config.json").read_text()
)
EPISODE_CONFIG = json.loads(
    (REPO_ROOT / "config/baselines/episode_build_config.json").read_text()
)


# ── Readers ──────────────────────────────────────────────────────────────────


def test_mcap_reader_yields_every_message_in_file_order() -> None:
    msgs = tiny.messages()
    out = list(iter_messages_from_bytes(tiny.mcap(msgs)))
    assert [(m.topic, m.log_time, m.data) for m in out] == msgs
    assert [m.file_index for m in out] == list(range(len(msgs)))
    assert {m.schema_name for m in out if m.topic == "/tf"} == {
        "tf2_msgs/msg/TFMessage"
    }
    images = [m for m in out if m.topic == "/camera/front/image/compressed"]
    assert [m.sequence for m in images] == [1, 2, 3]


def test_mcap_reader_refuses_truncated_bytes() -> None:
    data = tiny.mcap(tiny.messages())
    with pytest.raises(McapFormatError):
        list(iter_messages_from_bytes(data[:-3]))
    with pytest.raises(McapFormatError):
        list(iter_messages_from_bytes(data[:200] + tiny.mcap(tiny.messages())[-8:]))


def test_cdr_decoders_read_the_fields_the_rules_use() -> None:
    image = cdr.compressed_image(tiny.image(tiny.T0 + 7, b"\xff\xd8\xffjpeg"))
    assert (image.header.stamp_ns, image.header.frame_id, image.format, image.data) == (
        tiny.T0 + 7,
        "cam_front",
        "jpeg",
        b"\xff\xd8\xffjpeg",
    )
    cloud = cdr.point_cloud2(tiny.cloud(tiny.T0, b"\x01" * 40))
    assert (cloud.point_step, cloud.width, cloud.data) == (20, 2, b"\x01" * 40)
    (t,) = cdr.tf_message(
        tiny.tf([(tiny.T0, "map", "base_link", [1.0, 2.0, 3.0], [0.5, 0.1, 0.2, 0.3])])
    )
    assert (t.parent_frame_id, t.child_frame_id, t.translation_m, t.rotation_wxyz) == (
        "map",
        "base_link",
        [1.0, 2.0, 3.0],
        [0.5, 0.1, 0.2, 0.3],
    )
    info = cdr.camera_info(tiny.camera_info(tiny.T0))
    assert (info.width, info.height, info.k) == (1600, 900, tiny.K)
    odom = cdr.odometry(tiny.odom(tiny.T0, 1.5, 2.5, 3.5))
    assert (odom.position[:2], odom.linear[0]) == ([1.5, 2.5], 3.5)
    assert cdr.battery_state(tiny.battery(tiny.T0, 0.91)).percentage == pytest.approx(
        0.91
    )
    assert json.loads(cdr.string_message(tiny.string({"a": 1}))) == {"a": 1}


# ── Rules ────────────────────────────────────────────────────────────────────


def test_composition_checksum_is_a_function_of_the_pin_set() -> None:
    pins = [
        ("scene", "scene-a", "sha256:" + "1" * 64),
        ("episode", "episode-b", "sha256:" + "2" * 64),
    ]
    assert rules.composition_checksum(pins) == rules.composition_checksum(
        reversed(pins)
    )
    assert rules.composition_checksum(pins) != rules.composition_checksum(pins[:1])


def test_unit_identity_is_the_documented_document() -> None:
    import hashlib

    document = b'{"domain":"scene","source_id":"run-x","source_kind":"recording","unit_id_schema":"sceneops.unit_id/v2","unit_key":"k"}'
    assert (
        rules.unit_id(
            domain="scene", source_kind="recording", source_id="run-x", unit_key="k"
        )
        == "scene-" + hashlib.sha256(document).hexdigest()[:32]
    )
    key = rules.recording_unit_key(
        boundary={"policy": "whole_recording", "clock": "c"}, segment="recording"
    )
    doc = b'{"clock":"c","policy":"whole_recording","recording_segmentation_schema":"sceneops.recording_segmentation/v1"}'
    assert (
        key == "whole_recording-" + hashlib.sha256(doc).hexdigest()[:16] + "/recording"
    )


# ── Expectations ─────────────────────────────────────────────────────────────


def test_scene_expectation_follows_the_documented_rules() -> None:
    facts = recording_facts_from_bytes(tiny.mcap(tiny.messages()))
    scene = expected_scene(facts, SCENE_CONFIG)
    assert scene.unit_key.endswith("/recording")
    assert scene.window == {
        "source_clock": "sensor.header_stamp",
        "start_timestamp_ns": tiny.T0,
        "end_timestamp_ns": tiny.T0 + 110_000_000 + 1,
    }
    assert [len(scene.observations[t]) for t in sorted(scene.observations)] == [3, 2]
    assert len(scene.poses) == 4
    camera = scene.observations["/camera/front/image/compressed"][0]
    assert camera["payload"]["media_type"] == "image/jpeg" and camera["image_size"] == [
        1600,
        900,
    ]
    assert camera["payload"]["checksum"] == rules.checksum(b"\xff\xd8\xffa0")
    lidar = scene.observations["/lidar/top/points"][0]["payload"]
    # ADR-007 §30.4: the recorded type sensor_msgs/msg/PointCloud2 is named lowercased.
    assert lidar["media_type"] == "application/x.ros2-cdr.sensor_msgs.msg.pointcloud2"
    assert scene.coordinate_frames == [
        ["base_link", "ego"],
        ["cam_front", "sensor"],
        ["lidar_top", "sensor"],
        ["map", "world"],
    ]


def test_episode_segments_pair_markers_and_number_occurrences() -> None:
    facts = recording_facts_from_bytes(tiny.mcap(tiny.messages(second_task=True)))
    episodes = expected_episodes(facts, EPISODE_CONFIG)
    assert [u.segment for u in episodes.units] == [
        "task-mission-a-000",
        "task-mission-a-001",
    ]
    first = episodes.units[0]
    assert first.window["end_timestamp_ns"] == tiny.T0 + 200_000_000 + 1
    assert [r["values"] for r in first.occurrences["actions"]["/vehicle/control"]] == [
        {"brake": 0, "steering": 3.0, "throttle": 0}
    ]
    assert [
        r["values"]["state"] for r in first.occurrences["events"]["/mission/status"]
    ] == ["running", "completed"]
    second = episodes.units[1]
    assert second.occurrences["observations"]["/camera/front/image/compressed"] == []


def test_an_unpaired_marker_has_no_expectation() -> None:
    msgs = [
        m
        for m in tiny.messages()
        if not (m[0] == "/mission/status" and b"completed" in m[2])
    ]
    with pytest.raises(ExpectationError):
        expected_episodes(recording_facts_from_bytes(tiny.mcap(msgs)), EPISODE_CONFIG)


# ── Synthetic platform state and negative controls ───────────────────────────


@pytest.fixture(scope="module")
def tiny_world(tmp_path_factory):
    root = tmp_path_factory.mktemp("tiny")
    fixtures = {
        "fx-a": tiny.mcap(tiny.messages("a")),
        "fx-b": tiny.mcap(tiny.messages("b")),
    }
    corpus_dir, cache = tiny.write_corpus(
        root, fixtures, tiny.contract(sorted(fixtures))
    )
    sources = load_sources(corpus_dir)
    derived = {
        fid: derive(sources, recording_facts_from_bytes(data))
        for fid, data in fixtures.items()
    }
    obs = reference_verify.synthetic_observation(sources, derived)
    captured = {
        sources.run_id("streaming_acquisition", fid): d.facts
        for fid, d in derived.items()
    }
    return sources, derived, obs, captured


def _codes(world, obs, fixtures=None):
    sources, derived, _, captured = world
    return reference_verify._verify(
        sources, obs, derived, captured=captured, deep=True, fixtures=fixtures
    ).violations


def test_the_documented_platform_state_passes(tiny_world) -> None:
    assert _codes(tiny_world, tiny_world[2]) == []


@pytest.mark.parametrize("control", negative.PLATFORM_CONTROLS, ids=lambda c: c.name)
def test_every_platform_negative_control_is_detected(tiny_world, control) -> None:
    sources, _, obs, _ = tiny_world
    corrupted = copy.deepcopy(obs)
    control.apply(
        corrupted,
        {
            "run_id": sources.run_id("recording_import", "fx-a"),
            "other_run_id": sources.run_id("streaming_acquisition", "fx-a"),
            "dataset_key": sources.dataset_key("recording_import"),
        },
    )
    codes = {v["code"] for v in _codes(tiny_world, corrupted, fixtures=["fx-a"])}
    assert codes & control.codes, f"{control.name}: observed {sorted(codes)}"


RECORDING_ONLY = ("recording_import",)


def test_a_recording_import_state_passes_when_only_that_mode_is_verified(
    tiny_world,
) -> None:
    sources, derived, _, captured = tiny_world
    obs = reference_verify.synthetic_observation(sources, derived, RECORDING_ONLY)
    assert set(obs["robot_runs"]) == {
        sources.run_id("recording_import", fid) for fid in sources.fixture_ids()
    }
    found = reference_verify._verify(
        sources, obs, derived, captured={}, deep=True, modes=RECORDING_ONLY
    )
    assert found.ok, found.violations[:3]
    # The same state is not the whole contract.
    assert not reference_verify._verify(
        sources, obs, derived, captured={}, deep=True
    ).ok


@pytest.mark.parametrize("control", negative.PLATFORM_CONTROLS, ids=lambda c: c.name)
def test_every_platform_negative_control_is_detected_for_recording_import_only(
    tiny_world, control
) -> None:
    sources, derived, _, _ = tiny_world
    obs = reference_verify.synthetic_observation(sources, derived, RECORDING_ONLY)
    corrupted = copy.deepcopy(obs)
    control.apply(corrupted, reference_verify.control_context(sources, RECORDING_ONLY))
    codes = reference_verify._verify(
        sources, corrupted, derived, captured={}, deep=True, fixtures=["fx-a"],
        modes=RECORDING_ONLY,
    ).codes()  # fmt: skip
    assert codes & control.codes, f"{control.name}: observed {sorted(codes)}"


def test_streaming_content_must_equal_the_locked_recording(tiny_world) -> None:
    sources, derived, obs, captured = tiny_world
    run_id = sources.run_id("streaming_acquisition", "fx-a")
    msgs = tiny.messages("a")
    shifted = [
        (t, lt + 1, d) if t == "/vehicle/status" else (t, lt, d) for t, lt, d in msgs
    ]
    other = dict(captured)
    other[run_id] = recording_facts_from_bytes(
        tiny.mcap(
            [m for m in shifted if m[0] != "/lidar/top/points"]
            + [m for m in msgs if m[0] == "/lidar/top/points"][:1]
        )
    )
    found = reference_verify._verify(
        sources, obs, derived, captured=other, deep=True, fixtures=["fx-a"]
    )
    assert "streaming_content" in found.codes()


def test_expectations_drift_is_detected(tiny_world) -> None:
    sources, derived, _, _ = tiny_world
    document = expectations_document(sources, derived)
    f = Findings()
    check_expectations(f, copy.deepcopy(document), document)
    assert f.ok
    for control in negative.expectation_controls():
        state = {"committed": copy.deepcopy(document)}
        control.apply(state, {})
        g = Findings()
        check_expectations(g, state["committed"], document)
        assert g.codes() & control.codes, control.name


# ── Collection (read-only, against fakes) ────────────────────────────────────


def _camel(value):
    if isinstance(value, dict):
        return {
            "".join(
                w if i == 0 else w.title() for i, w in enumerate(k.split("_"))
            ): _camel(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_camel(v) for v in value]
    return value


class _FakePlatform:
    """FastAPI + object store over a synthetic observation; records reads."""

    def __init__(self, obs, recordings):
        self.obs, self.recordings, self.objects = obs, recordings, {}
        self.artifacts = {}
        for run_id, run in obs["robot_runs"].items():
            uri = f"s3://b/runs/{run_id}.mcap"
            self.artifacts[run["record"]["recording_artifact_id"]] = {
                **run["recording_artifact"],
                "uri": uri,
            }
            self.objects[uri] = recordings[run_id]
        for artifact_id, m in obs["manifests"].items():
            uri = f"s3://b/m/{artifact_id}"
            self.artifacts[artifact_id] = {**m["artifact"], "uri": uri}
            self.objects[uri] = m["bytes"].encode()
        for artifact_id, p in obs["payloads"].items():
            uri = f"s3://b/p/{artifact_id}"
            self.artifacts[artifact_id] = {**p["artifact"], "uri": uri}
            self.objects[uri] = b"x" * p["artifact"]["size_bytes"]

    def __call__(self, path):
        path, _, query = path.partition("?")
        parts = path.strip("/").split("/")
        if parts[0] == "robot-runs":
            run = self.obs["robot_runs"].get(parts[1])
            return run and {"robotRun": _camel(run["record"])}
        if parts[0] == "artifacts":
            a = self.artifacts.get(parts[1])
            return a and {"artifact": _camel(a)}
        if parts[0] in ("scenes", "episodes"):
            domain = parts[0][:-1]
            if len(parts) == 1:
                rid = dict(kv.split("=") for kv in query.split("&"))["robot_run_id"]
                offset = int(dict(kv.split("=") for kv in query.split("&"))["offset"])
                rows = [
                    _camel(u["record"])
                    for u in self.obs["units"][domain].values()
                    if u["record"]["robot_run_id"] == rid
                ]
                return {parts[0]: rows[offset:]}
            unit = self.obs["units"][domain][parts[1]]
            revisions = [
                {
                    **{k: v for k, v in r.items() if k != "unit_id"},
                    f"{domain}_id": r["unit_id"],
                }
                for r in unit["revisions"]
            ]
            return {domain: _camel(unit["record"]), "revisions": _camel(revisions)}
        if parts[0] == "datasets":
            key = f"{parts[1]}/{parts[3]}"
            version = self.obs["dataset_versions"].get(key)
            if version is None:
                return None
            if len(parts) == 4:
                return {"version": _camel(version["version"])}
            offset = int(dict(kv.split("=") for kv in query.split("&"))["offset"])
            domain = parts[4][:-1]
            rows = [
                {
                    f"{domain}_id": m["unit_id"],
                    "manifest_artifact_id": m["manifest_artifact_id"],
                    "manifest_checksum": m["manifest_checksum"],
                }
                for m in version["members"]
                if m["domain"] == domain
            ]
            return {parts[4]: _camel(rows)[offset:]}
        raise AssertionError(path)

    def head(self, uri):
        data = self.objects.get(uri)
        return None if data is None else len(data)

    def get(self, uri):
        return self.objects.get(uri)


def test_collect_reads_what_the_checks_need(tiny_world) -> None:
    sources, derived, obs, _ = tiny_world
    recordings = {}
    for mode in ("recording_import", "streaming_acquisition"):
        for fid in sources.fixture_ids():
            recordings[sources.run_id(mode, fid)] = tiny.mcap(tiny.messages(fid[-1]))
    fake = _FakePlatform(obs, recordings)
    sunk = {}
    collected = observation.collect(
        fake, fake,
        robot_run_ids=list(obs["robot_runs"]),
        dataset_versions=[tuple(k.split("/")) for k in obs["dataset_versions"]],
        deep=False, recording_sink=lambda rid, data: sunk.setdefault(rid, data),
    )  # fmt: skip
    assert collected["units"] == obs["units"]
    assert collected["dataset_versions"].keys() == obs["dataset_versions"].keys()
    for key, version in obs["dataset_versions"].items():
        assert sorted(collected["dataset_versions"][key]["members"], key=str) == sorted(
            version["members"], key=str
        )
    assert {k: m["bytes"] for k, m in collected["manifests"].items()} == {
        k: m["bytes"] for k, m in obs["manifests"].items()
    }
    assert set(collected["payloads"]) == set(obs["payloads"])
    assert all(
        "checksum" not in p["object"] for p in collected["payloads"].values()
    )  # HEAD only unless deep
    assert set(sunk) == set(obs["robot_runs"])
    captured = {rid: recording_facts_from_bytes(data) for rid, data in sunk.items()}
    found = reference_verify._verify(
        sources, collected, derived, captured=captured, deep=False
    )
    assert found.ok, found.violations[:3]


# ── Real data (skipped without the locked corpus / raw nuScenes) ─────────────

REAL_CACHE = REPO_ROOT / "data/reference"
RAW = REPO_ROOT / "data/raw/nuscenes"
real = pytest.mark.skipif(
    not any((REAL_CACHE / "nuscenes-mini-v1/recordings").glob("scene-0061-*.mcap"))
    or not (RAW / "v1.0-mini").is_dir(),
    reason="needs the locked reference corpus and raw nuScenes v1.0-mini",
)


@real
def test_scene_0061_sources_and_committed_expectations(tmp_path) -> None:
    assert reference_verify.main(["sources", "--fixture", "scene-0061"]) == 0
    sources = load_sources()
    from verification.contract import derive_fixture

    d = derive_fixture(sources, "scene-0061", REAL_CACHE)
    committed = json.loads(sources.expectations_path.read_text())
    f = Findings()
    check_expectations(
        f,
        committed,
        expectations_document(sources, {"scene-0061": d}),
        fixtures=["scene-0061"],
    )
    assert f.ok, f.violations
