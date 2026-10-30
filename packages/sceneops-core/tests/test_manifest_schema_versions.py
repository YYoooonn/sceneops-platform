"""Canonical manifest versions (ADR-017): SceneManifest v5 and EpisodeManifest
v4 are the only versions, and every payload of every source is the unified
PayloadRef. Bytes of any other version, or a payload reference by artifact or
content id, are refused."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.artifacts.schemas import PayloadRef, VideoFrameSelector
from sceneops_core.episodes import testing as episode_testing
from sceneops_core.episodes.schemas import (
    EPISODE_MANIFEST_SCHEMA_V4,
    EpisodeManifest,
    EpisodeManifestError,
    UnsupportedEpisodeManifestVersionError,
    load_canonical_episode_manifest,
)
from sceneops_core.provenance import ContentUnitSource
from sceneops_core.scenes import testing as scene_testing
from sceneops_core.scenes.schemas import (
    SCENE_MANIFEST_SCHEMA_V5,
    SceneManifest,
    SceneManifestError,
    UnsupportedSceneManifestVersionError,
    load_canonical_scene_manifest,
)
from sceneops_core.sources import content_id_for

PAYLOAD_REF_FIELDS = {"uri", "checksum", "size_bytes", "media_type", "selector"}
CONTENT_CHECKSUM = "sha256:" + "c" * 64
CONTENT_ID = content_id_for(CONTENT_CHECKSUM)
VIDEO_URI = format_logical_uri(
    LogicalNamespace.SOURCES,
    f"contents/{CONTENT_ID}/files/videos/observation.image/chunk-000/file-000.mp4",
)


def _content_scene() -> SceneManifest:
    return scene_testing.build_scene_manifest(
        source=ContentUnitSource(
            content_id=CONTENT_ID,
            content_checksum=CONTENT_CHECKSUM,
            source_clock="mcap_log_time",
            start_timestamp_ns=0,
            end_timestamp_ns=10_000_000_000,
            unit_key="cc8c0bf57f984915a77078b10eb33198",
        )
    )


def _episode() -> EpisodeManifest:
    return episode_testing.episode_manifest(
        [
            episode_testing.observation("/camera", 10),
            episode_testing.observation("/camera", 20),
            episode_testing.state("/joint", 15, position=1.5),
        ]
    )


def _scene_payload_dicts(data: bytes) -> list[dict]:
    return [o["payload"] for o in json.loads(data)["observations"]]


def _episode_payload_dicts(data: bytes) -> list[dict]:
    document = json.loads(data)
    return [
        item["payload"]
        for name in ("observations", "states", "actions", "events", "transitions")
        for item in document[name]
        if item.get("payload") is not None
    ]


def _with_version(data: bytes, version) -> bytes:
    document = json.loads(data)
    document["schema_version"] = version
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


# --- the current versions ---------------------------------------------------------------


@pytest.mark.parametrize(
    "build", [scene_testing.build_scene_manifest, _content_scene]
)  # fmt: skip
def test_a_v5_scene_round_trips_canonically_and_deterministically(build):
    data = build().to_canonical_bytes()
    assert json.loads(data)["schema_version"] == SCENE_MANIFEST_SCHEMA_V5
    assert load_canonical_scene_manifest(data) == build()
    assert load_canonical_scene_manifest(data).to_canonical_bytes() == data
    assert build().to_canonical_bytes() == data


def test_a_v4_episode_round_trips_canonically_and_deterministically():
    data = _episode().to_canonical_bytes()
    assert json.loads(data)["schema_version"] == EPISODE_MANIFEST_SCHEMA_V4
    assert load_canonical_episode_manifest(data) == _episode()
    assert load_canonical_episode_manifest(data).to_canonical_bytes() == data


def test_manifests_are_built_in_the_current_version_by_default():
    assert SceneManifest.model_fields["schema_version"].default == (
        SCENE_MANIFEST_SCHEMA_V5
    )
    assert EpisodeManifest.model_fields["schema_version"].default == (
        EPISODE_MANIFEST_SCHEMA_V4
    )


def test_every_source_holds_the_same_payload_schema():
    recording, content = scene_testing.build_scene_manifest(), _content_scene()
    payloads = [
        *(o.payload for m in (recording, content) for o in m.observations),
        *(o.payload for o in _episode().occurrences() if o.payload is not None),
    ]
    assert {type(p) for p in payloads} == {PayloadRef}
    dicts = [
        *_scene_payload_dicts(recording.to_canonical_bytes()),
        *_scene_payload_dicts(content.to_canonical_bytes()),
        *_episode_payload_dicts(_episode().to_canonical_bytes()),
    ]
    assert {frozenset(d) for d in dicts} == {frozenset(PAYLOAD_REF_FIELDS)}
    namespaces = {
        m.lineage.source.source_kind: {
            o.payload.logical_uri().namespace for o in m.observations
        }
        for m in (recording, content)
    }
    assert namespaces == {
        "recording": {LogicalNamespace.OBSERVATION_PAYLOADS},
        "content": {LogicalNamespace.SOURCES},
    }


def test_a_video_frame_selector_is_carried_through_the_canonical_bytes():
    document = json.loads(_episode().to_canonical_bytes())
    frame = {"kind": "video_frame", "timestamp_ns": 33_333_333}
    for item in document["observations"]:
        item["payload"].update(uri=VIDEO_URI, media_type="video/mp4", selector=frame)
    manifest = EpisodeManifest.model_validate(document)
    data = manifest.to_canonical_bytes()
    assert load_canonical_episode_manifest(data) == manifest
    assert {o.payload.selector for o in manifest.observations} == {
        VideoFrameSelector(timestamp_ns=33_333_333)
    }


# --- earlier and unknown versions ---------------------------------------------------------


@pytest.mark.parametrize(
    "version",
    [
        "sceneops.scene_manifest/v4",
        "sceneops.scene_manifest/v3",
        "sceneops.scene_manifest/v6",
        "sceneops.episode_manifest/v4",
        None,
        5,
    ],
)
def test_any_other_scene_version_is_refused(version):
    data = scene_testing.build_scene_manifest().to_canonical_bytes()
    with pytest.raises(UnsupportedSceneManifestVersionError):
        load_canonical_scene_manifest(_with_version(data, version))


@pytest.mark.parametrize(
    "version",
    [
        "sceneops.episode_manifest/v3",
        "sceneops.episode_manifest/v5",
        "sceneops.episode_manifest/v2",
        "sceneops.scene_manifest/v5",
        None,
    ],
)
def test_any_other_episode_version_is_refused(version):
    data = _episode().to_canonical_bytes()
    with pytest.raises(UnsupportedEpisodeManifestVersionError):
        load_canonical_episode_manifest(_with_version(data, version))


LEGACY_SHAPED = [
    {"kind": "artifact", "artifact_id": "payload-0001",
     "checksum": "sha256:" + "a" * 64, "size_bytes": 15, "media_type": "image/jpeg"},
    {"kind": "source_file", "content_id": CONTENT_ID, "path": "samples/a.jpg",
     "sha256": "sha256:" + "a" * 64, "size_bytes": 15, "media_type": "image/jpeg",
     "frame": None},
]  # fmt: skip


@pytest.mark.parametrize("legacy", LEGACY_SHAPED)
def test_a_reference_by_artifact_or_content_id_is_refused(legacy):
    scene = json.loads(scene_testing.build_scene_manifest().to_canonical_bytes())
    scene["observations"][0]["payload"] = legacy
    with pytest.raises(SceneManifestError):
        load_canonical_scene_manifest(
            json.dumps(scene, separators=(",", ":"), sort_keys=True).encode()
        )
    episode = json.loads(_episode().to_canonical_bytes())
    episode["observations"][0]["payload"] = legacy
    with pytest.raises(EpisodeManifestError):
        load_canonical_episode_manifest(
            json.dumps(episode, separators=(",", ":"), sort_keys=True).encode()
        )


def test_a_v5_manifest_must_be_canonical():
    data = scene_testing.build_scene_manifest().to_canonical_bytes()
    pretty = json.dumps(json.loads(data), indent=1).encode()
    with pytest.raises(
        SceneManifestError, match="canonical sceneops.scene_manifest/v5"
    ):
        load_canonical_scene_manifest(pretty)


# --- semantic validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["observations"][0].update(channel="A_NOWHERE"), "unknown"),
        (
            lambda d: d["observations"][0].update(calibration_id="cal-nowhere"),
            "calibration",
        ),
        (lambda d: d["observations"][0]["payload"].update(uri="s3://b/x"), "sceneops"),
        (
            lambda d: d["observations"][0]["payload"].update(
                selector={"kind": "video_frame", "timestamp_ns": 0}
            ),
            "cannot select part of",
        ),
        (
            lambda d: d["observations"][0]["payload"].update(size_bytes=-1),
            "greater than or equal",
        ),
    ],
)
def test_invalid_semantic_references_are_refused(mutate, match):
    document = json.loads(_content_scene().to_canonical_bytes())
    mutate(document)
    with pytest.raises(ValidationError, match=match):
        SceneManifest.model_validate(document)


def test_the_schema_does_not_branch_on_the_source_kind():
    """What a URI names (the unit's own recording payloads or content files)
    is a registration fact; the schema validates the reference itself."""
    document = json.loads(scene_testing.build_scene_manifest().to_canonical_bytes())
    foreign = _content_scene().observations[0].payload
    document["observations"][0]["payload"] = foreign.model_dump(mode="json")
    assert SceneManifest.model_validate(document).observations[0].payload == foreign
