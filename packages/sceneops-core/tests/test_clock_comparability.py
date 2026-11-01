"""Clock comparability by origin instance across sources (ADR-015 §6.2,
OQ-9): which units' timestamps may be compared, decided by the clock id, its
declared origin and the origin scope the unit's source binding asserts.

Every matrix case also checks each declared clock's instance against
``_expected_instance``: the scope key spelled out from the binding's fields
(``()`` / ``(source_kind, source_id)`` / ``(source_kind, source_id,
unit_key)``), so the binding-owned scope stays exactly the comparability the
manifests have always asserted."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeManifestError,
    load_canonical_episode_manifest,
)
from sceneops_core.episodes.testing import episode_manifest, state
from sceneops_core.provenance import (
    ContentUnitSource,
    ProducerInfo,
    RecordingSegmentSource,
)
from sceneops_core.scenes.schemas import (
    SceneManifest,
    SceneManifestError,
    load_canonical_scene_manifest,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources import content_id_for
from sceneops_core.temporal import (
    ClockDomain,
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    OriginScope,
    clock_instance,
    frame_time_ns,
    require_comparable,
)
from sceneops_core.temporal import clocks as clocks_module

NUSCENES_CLOCK = "nuscenes.timestamp"
LEROBOT_CLOCK = "lerobot.frame_time"


def _expected_instance(clock: str, origin: ClockOrigin, source: Any) -> ClockInstance:
    if origin == ClockOrigin.UNIX_EPOCH:
        scope: tuple[str, ...] = ()
    elif origin == ClockOrigin.SOURCE:
        scope = (source.source_kind, source.source_id)
    else:
        scope = (source.source_kind, source.source_id, source.unit_key)
    return ClockInstance(clock=clock, origin=origin, scope=scope)


def _instance(manifest: SceneManifest | EpisodeManifest, clock: str) -> ClockInstance:
    """``manifest.clock_instance(clock)``, checked against the binding's
    fields for every clock the manifest declares."""
    source = manifest.lineage.source
    for domain in manifest.clock_domains:
        assert manifest.clock_instance(domain.clock) == _expected_instance(
            domain.clock, domain.origin, source
        )
    return manifest.clock_instance(clock)


def _comparable(a: ClockInstance, b: ClockInstance) -> bool:
    try:
        require_comparable(a, b)
    except IncomparableClocksError:
        assert a != b
        return False
    assert a == b
    return True


def _checksum(seed: str) -> str:
    return "sha256:" + seed * 64


def _content_source(
    checksum: str, unit_key: str, *, clock: str, end_ns: int = 10_000_000_000
) -> ContentUnitSource:
    return ContentUnitSource(
        content_id=content_id_for(checksum),
        content_checksum=checksum,
        source_clock=clock,
        start_timestamp_ns=0,
        end_timestamp_ns=end_ns,
        unit_key=unit_key,
    )


def _nuscenes_scene(
    checksum: str, unit_key: str, *, origin: str = "unix_epoch"
) -> SceneManifest:
    """A content Scene on one nuScenes-style clock with ``origin``."""
    source = _content_source(checksum, unit_key, clock=NUSCENES_CLOCK)
    payload = json.loads(build_scene_manifest(source=source).to_canonical_bytes())
    for domain in payload["clock_domains"]:
        domain["origin"] = origin
    return SceneManifest.model_validate(payload)


def _lerobot_document(
    checksum: str, unit_key: str, *, origin: str = "unit", frames: int = 3
) -> dict[str, Any]:
    """A content Episode on a frame grid, LeRobot style: frame 0 is the
    unit's origin."""
    fps = 10
    source = _content_source(
        checksum,
        unit_key,
        clock=LEROBOT_CLOCK,
        end_ns=frame_time_ns(frames - 1, fps) + 1,
    )
    producer = ProducerInfo.create(
        producer_id="sceneops.lerobot_episode_builder",
        semantics_version=1,
        build_config={"features": []},
        source=source.source_revision(),
    )
    return {
        "schema_version": "sceneops.episode_manifest/v4",
        "lineage": {
            "source": source.model_dump(mode="json"),
            "producer": producer.model_dump(mode="json"),
        },
        "clock_domains": [
            {"clock": LEROBOT_CLOCK, "origin": origin, "resolution_ns": 1}
        ],
        "frame_timeline": {"clock": LEROBOT_CLOCK, "fps": fps, "frame_count": frames},
        "streams": [
            {
                "topic": "observation.state",
                "role": "state",
                "schema_name": "float32",
                "source_clock": LEROBOT_CLOCK,
                "fields": [{"name": "value", "path": "observation.state"}],
            }
        ],
        "states": [
            {
                "occurrence_id": f"observation.state-{k:08d}",
                "topic": "observation.state",
                "timestamp_ns": frame_time_ns(k, fps),
                "values": {"value": [float(k)]},
            }
            for k in range(frames)
        ],
    }


def _lerobot_episode(checksum: str, unit_key: str) -> EpisodeManifest:
    return EpisodeManifest.model_validate(_lerobot_document(checksum, unit_key))


# --- 1. independent RobotRuns ---------------------------------------------------


def test_two_robot_runs_on_one_clock_id_are_incomparable():
    a = build_scene_manifest(source=recording_source(robot_run_id="run-001"))
    b = build_scene_manifest(source=recording_source(robot_run_id="run-002"))
    assert not _comparable(_instance(a, "mcap_log_time"), _instance(b, "mcap_log_time"))
    episode_a = episode_manifest([state("/odom", 1)], robot_run_id="run-001")
    episode_b = episode_manifest([state("/odom", 1)], robot_run_id="run-002")
    assert not _comparable(
        _instance(episode_a, "sensor.header_stamp"),
        _instance(episode_b, "sensor.header_stamp"),
    )


# --- 2. segments of one RobotRun ------------------------------------------------


def test_segments_of_one_robot_run_share_its_source_clocks():
    a = build_scene_manifest(
        source=recording_source(unit_key="segment-0000"),
        camera_clock="camera.header_stamp",
    )
    b = build_scene_manifest(
        source=recording_source(
            unit_key="segment-0001",
            start_timestamp_ns=10_000_000_000,
            end_timestamp_ns=20_000_000_000,
        ),
        camera_clock="camera.header_stamp",
        keyframe_timestamps_ns=(11_000_000_000, 11_500_000_000),
    )
    for clock in ("mcap_log_time", "camera.header_stamp"):
        assert _comparable(_instance(a, clock), _instance(b, clock))
    assert _instance(a, "mcap_log_time") == ClockInstance(
        clock="mcap_log_time", origin=ClockOrigin.SOURCE, scope=("recording", "run-001")
    )
    # One run, two clocks: still two domains.
    assert not _comparable(
        _instance(a, "mcap_log_time"), _instance(b, "camera.header_stamp")
    )
    episode_a = episode_manifest([state("/odom", 1)], unit_key="segment-0000")
    episode_b = episode_manifest([state("/odom", 1)], unit_key="segment-0001")
    assert _comparable(
        _instance(episode_a, "sensor.header_stamp"),
        _instance(episode_b, "sensor.header_stamp"),
    )


# --- 3. a Scene and an Episode of one RobotRun ----------------------------------


def test_a_scene_and_an_episode_of_one_robot_run_compare_on_shared_clocks():
    scene = build_scene_manifest(source=recording_source(robot_run_id="run-001"))
    episode = episode_manifest(
        [state("/odom", 1_000), state("/cmd", 2_000, clock="mcap_log_time")],
        robot_run_id="run-001",
        window=(0, 10_000),
        window_clock="mcap_log_time",
    )
    assert _comparable(
        _instance(scene, "mcap_log_time"), _instance(episode, "mcap_log_time")
    )
    assert not _comparable(
        _instance(scene, "mcap_log_time"), _instance(episode, "sensor.header_stamp")
    )
    other_run = episode_manifest(
        [state("/cmd", 2_000, clock="mcap_log_time")],
        robot_run_id="run-002",
        window=(0, 10_000),
        window_clock="mcap_log_time",
    )
    assert not _comparable(
        _instance(scene, "mcap_log_time"), _instance(other_run, "mcap_log_time")
    )


# --- 4. nuScenes contents on unix_epoch -----------------------------------------


def test_contents_on_one_unix_epoch_clock_are_comparable():
    a = _nuscenes_scene(_checksum("a"), "scene-0061")
    b = _nuscenes_scene(_checksum("b"), "scene-0103")
    assert a.lineage.source.source_id != b.lineage.source.source_id
    assert _comparable(_instance(a, NUSCENES_CLOCK), _instance(b, NUSCENES_CLOCK))
    assert _instance(a, NUSCENES_CLOCK).scope == ()
    # unix_epoch is absolute everywhere, a RobotRun's too.
    assert _comparable(
        clock_instance(
            a.clock_domain(NUSCENES_CLOCK), recording_source().origin_scope()
        ),
        _instance(b, NUSCENES_CLOCK),
    )


# --- 5. LeRobot Episodes on unit clocks -----------------------------------------


def test_lerobot_episodes_on_a_unit_clock_are_incomparable():
    content = _checksum("c")
    first = _lerobot_episode(content, "0")
    second = _lerobot_episode(content, "1")
    assert not _comparable(
        _instance(first, LEROBOT_CLOCK), _instance(second, LEROBOT_CLOCK)
    )
    # The same episode index of other content is another unit too.
    elsewhere = _lerobot_episode(_checksum("d"), "0")
    assert not _comparable(
        _instance(first, LEROBOT_CLOCK), _instance(elsewhere, LEROBOT_CLOCK)
    )
    # The one unit, parsed again, is its own origin instance.
    reread = load_canonical_episode_manifest(first.to_canonical_bytes())
    assert _comparable(
        _instance(first, LEROBOT_CLOCK), _instance(reread, LEROBOT_CLOCK)
    )


# --- 6. source vs unix_epoch on one clock id ------------------------------------


def test_source_and_unix_epoch_origins_of_one_clock_id_are_incomparable():
    content = _checksum("a")
    absolute = _nuscenes_scene(content, "scene-0061", origin="unix_epoch")
    relative = _nuscenes_scene(content, "scene-0061", origin="source")
    assert not _comparable(
        _instance(absolute, NUSCENES_CLOCK), _instance(relative, NUSCENES_CLOCK)
    )
    run = build_scene_manifest(source=recording_source(source_clock="mcap_log_time"))
    epoch = ClockDomain(clock="mcap_log_time", origin="unix_epoch", resolution_ns=1)
    assert not _comparable(
        _instance(run, "mcap_log_time"),
        clock_instance(epoch, run.lineage.source.origin_scope()),
    )


# --- 7. equal timestamps do not make unit clocks comparable ----------------------


def test_equal_timestamps_never_make_unit_clocks_comparable():
    content = _checksum("c")
    first = _lerobot_episode(content, "0")
    second = _lerobot_episode(content, "1")
    assert [o.timestamp_ns for o in first.states] == [
        o.timestamp_ns for o in second.states
    ]
    assert first.declared_window() == second.declared_window()
    assert first.clock_domains == second.clock_domains
    assert not _comparable(
        _instance(first, LEROBOT_CLOCK), _instance(second, LEROBOT_CLOCK)
    )


# --- 8. recording origins are refused at parse time -----------------------------


@pytest.mark.parametrize("origin", ["unix_epoch", "unit"])
def test_a_recording_scene_asserting_another_origin_is_refused_when_parsed(origin):
    payload = json.loads(build_scene_manifest().to_canonical_bytes())
    payload["clock_domains"][0]["origin"] = origin
    with pytest.raises(SceneManifestError, match="origin 'source'"):
        load_canonical_scene_manifest(canonical_json_bytes(payload))
    with pytest.raises(ValidationError, match="origin 'source'"):
        SceneManifest.model_validate(payload)


@pytest.mark.parametrize("origin", ["unix_epoch", "unit"])
def test_a_recording_episode_asserting_another_origin_is_refused_when_parsed(origin):
    payload = json.loads(episode_manifest([state("/odom", 1_000)]).to_canonical_bytes())
    payload["clock_domains"][0]["origin"] = origin
    with pytest.raises(EpisodeManifestError, match="origin 'source'"):
        load_canonical_episode_manifest(canonical_json_bytes(payload))


@pytest.mark.parametrize("origin", ["unix_epoch", "source", "unit"])
def test_a_content_binding_admits_every_origin(origin):
    manifest = _nuscenes_scene(_checksum("a"), "scene-0061", origin=origin)
    assert load_canonical_scene_manifest(manifest.to_canonical_bytes()) == manifest


def test_bindings_state_their_permitted_origins_and_scopes():
    assert RecordingSegmentSource.permitted_clock_origins == {ClockOrigin.SOURCE}
    assert ContentUnitSource.permitted_clock_origins == set(ClockOrigin)
    run = recording_source(robot_run_id="run-007", unit_key="segment-0003")
    assert run.origin_scope() == OriginScope(
        source=("recording", "run-007"), unit=("recording", "run-007", "segment-0003")
    )
    content = _content_source(_checksum("a"), "0", clock=LEROBOT_CLOCK)
    assert content.origin_scope() == OriginScope(
        source=("content", content.content_id),
        unit=("content", content.content_id, "0"),
    )


def test_an_origin_scope_never_names_the_global_instance():
    with pytest.raises(ValueError, match="non-empty"):
        OriginScope(source=(), unit=("recording", "run-001", "segment-0000"))
    with pytest.raises(ValueError, match="non-empty"):
        OriginScope(source=("recording", "run-001"), unit=())


# --- 9. frame timeline origin ---------------------------------------------------


@pytest.mark.parametrize("origin", ["unix_epoch", "source"])
def test_a_frame_timeline_clock_must_have_unit_origin(origin):
    document = _lerobot_document(_checksum("c"), "0", origin=origin)
    with pytest.raises(ValidationError, match="must have origin 'unit'"):
        EpisodeManifest.model_validate(document)


def test_a_recording_episode_cannot_carry_a_frame_timeline():
    """A frame timeline needs a ``unit`` clock, which a recording never
    declares, so a recording Episode has no frame grid either way."""
    manifest = episode_manifest([state("/odom", 0), state("/odom", 100_000_000)])
    payload = json.loads(manifest.to_canonical_bytes())
    window = payload["lineage"]["source"]
    payload["frame_timeline"] = {
        "clock": window["source_clock"],
        "fps": 10,
        "frame_count": 2,
    }
    with pytest.raises(ValidationError, match="must have origin 'unit'"):
        EpisodeManifest.model_validate(payload)
    payload["clock_domains"] = [
        {**d, "origin": "unit"} for d in payload["clock_domains"]
    ]
    with pytest.raises(ValidationError, match="origin 'source'"):
        EpisodeManifest.model_validate(payload)


# --- 10. timestamps, windows and empty channels ---------------------------------


def test_declared_windows_and_timestamp_rules_are_the_bindings():
    scene = build_scene_manifest()
    source = scene.lineage.source
    window = scene.declared_window()
    assert (
        window.source_clock,
        window.start_timestamp_ns,
        window.end_timestamp_ns,
    ) == (
        source.source_clock,
        source.start_timestamp_ns,
        source.end_timestamp_ns,
    )
    payload = json.loads(scene.to_canonical_bytes())
    payload["observations"][-1]["timestamp_ns"] = source.end_timestamp_ns
    with pytest.raises(ValidationError, match="outside the unit window"):
        SceneManifest.model_validate(payload)

    episode = _lerobot_episode(_checksum("c"), "0")
    assert (
        episode.declared_window().start_timestamp_ns,
        episode.declared_window().end_timestamp_ns,
    ) == episode.frame_timeline.window()
    document = _lerobot_document(_checksum("c"), "0")
    document["clock_domains"][0]["resolution_ns"] = 1_000
    # Frame times at 10 fps are multiples of 10⁸ ns; frame 0 and the window
    # bound pass, so only an off-resolution timestamp is refused.
    document["states"][1]["timestamp_ns"] += 1
    with pytest.raises(ValidationError, match="not a multiple"):
        EpisodeManifest.model_validate(document)


def test_a_stream_without_occurrences_keeps_its_declared_comparable_clock():
    manifest = episode_manifest(
        [state("/odom", 1_000), state("/cmd", 2_000, clock="mcap_log_time")],
        window=(0, 10_000),
        window_clock="mcap_log_time",
    )
    payload = json.loads(manifest.to_canonical_bytes())
    payload["states"] = [o for o in payload["states"] if o["topic"] != "/odom"]
    empty = EpisodeManifest.model_validate(payload)
    assert empty.observed_topics(empty.stream("/odom").role) == ["/cmd"]
    assert _comparable(
        _instance(empty, "sensor.header_stamp"),
        _instance(manifest, "sensor.header_stamp"),
    )
    payload["clock_domains"] = [
        d for d in payload["clock_domains"] if d["clock"] != "sensor.header_stamp"
    ]
    with pytest.raises(ValidationError, match="used but not declared"):
        EpisodeManifest.model_validate(payload)


def test_a_channel_without_observations_keeps_its_declared_clock():
    scene = build_scene_manifest(camera_clock="camera.header_stamp")
    payload = json.loads(scene.to_canonical_bytes())
    camera = next(
        c["channel"] for c in payload["channels"] if c["modality"] == "camera"
    )
    dropped = {
        o["observation_id"] for o in payload["observations"] if o["channel"] == camera
    }
    payload["observations"] = [
        o for o in payload["observations"] if o["observation_id"] not in dropped
    ]
    for group in payload["groups"]:
        group["observation_ids"] = [
            i for i in group["observation_ids"] if i not in dropped
        ]
    empty = SceneManifest.model_validate(payload)
    assert camera not in empty.observed_channel_names()
    assert _comparable(
        _instance(empty, "camera.header_stamp"), _instance(scene, "camera.header_stamp")
    )


# --- temporal module dependency direction ---------------------------------------


def test_the_clock_module_knows_no_source_binding():
    """Origin authority belongs to the binding: the clock module imports no
    binding or source-registry model and reads no source field."""
    tree = ast.parse(Path(clocks_module.__file__).read_text())
    platform_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = ["." * node.level + (node.module or "")]
        else:
            continue
        platform_imports += [m for m in modules if m.startswith((".", "sceneops"))]
    # Only the platform's generic identifier rules; nothing of provenance,
    # sources or robots.
    assert platform_imports == ["sceneops_core.common.identifiers"]
    source_fields = {
        "SourceBinding",
        "RecordingSegmentSource",
        "ContentUnitSource",
        "source_kind",
        "source_id",
        "robot_run_id",
        "content_id",
        "unit_key",
    }
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (names | attributes) & source_fields
