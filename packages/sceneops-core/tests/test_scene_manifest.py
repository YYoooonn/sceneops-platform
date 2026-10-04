"""SceneManifest v1 contract: canonical bytes, strict parsing, source
fidelity invariants and record projection (ADR-007 §13.5-§13.8, §19,
I-1/I-3/I-21-I-24/I-26)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.schemas import PayloadRef
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.provenance import ProducerInfo
from sceneops_core.scenes.schemas import (
    SCENE_MANIFEST_SCHEMA_V1,
    NonCanonicalSceneManifestError,
    SceneChannel,
    SceneCoordinateFrame,
    SceneManifest,
    SceneManifestError,
    SceneTimeWindow,
    UnsupportedSceneManifestVersionError,
    load_canonical_scene_manifest,
    project_scene_record,
    scene_id_for,
)
from sceneops_core.scenes.testing import (
    build_scene_manifest,
    recording_source,
)


def _dump(manifest: SceneManifest) -> dict:
    return json.loads(manifest.to_canonical_bytes())


def _rebuild(payload: dict) -> SceneManifest:
    return SceneManifest.model_validate(payload)


# --- canonical serialization ---------------------------------------------------


def test_canonical_round_trip_and_determinism():
    first = build_scene_manifest()
    second = build_scene_manifest()

    data = first.to_canonical_bytes()
    assert data == second.to_canonical_bytes()
    assert load_canonical_scene_manifest(data) == first
    assert first.checksum() == sha256_checksum(data)
    assert not data.endswith(b"\n")
    assert b" " not in data.replace(b"vehicle.car", b"")


def test_manifest_holds_no_membership_or_execution_state():
    payload = _dump(build_scene_manifest())
    for forbidden in (
        "dataset_id",
        "dataset_version",
        "scene_id",
        "status",
        "metadata",
    ):
        assert forbidden not in payload
    assert payload["schema_version"] == SCENE_MANIFEST_SCHEMA_V1


def test_non_canonical_bytes_are_rejected_even_when_semantically_equal():
    manifest = build_scene_manifest()
    canonical = _dump(manifest)

    pretty = json.dumps(canonical, indent=2, sort_keys=True).encode()
    unsorted_keys = json.dumps(
        dict(reversed(list(canonical.items()))), separators=(",", ":")
    ).encode()
    without_defaulted_field = {k: v for k, v in canonical.items() if k != "poses"}
    without_defaulted_field["observations"] = [
        {**o, "ego_pose_id": None} for o in canonical["observations"]
    ]
    missing_default = json.dumps(
        without_defaulted_field, separators=(",", ":"), sort_keys=True
    ).encode()
    # The last one is a valid manifest, just not in canonical form.
    assert SceneManifest.model_validate(without_defaulted_field).poses == []

    for data in (
        pretty,
        unsorted_keys,
        missing_default,
        manifest.to_canonical_bytes() + b"\n",
    ):
        with pytest.raises(NonCanonicalSceneManifestError):
            load_canonical_scene_manifest(data)


def test_integer_written_where_a_float_is_canonical_is_rejected():
    data = build_scene_manifest().to_canonical_bytes()
    tampered = data.replace(
        b'"translation_m":[1.7,0.0,1.5]', b'"translation_m":[1.7,0,1.5]', 1
    )
    assert tampered != data
    with pytest.raises(NonCanonicalSceneManifestError):
        load_canonical_scene_manifest(tampered)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(metadata={}),
        lambda p: p["observations"][0].update(source_sample_data_id="x"),
        lambda p: p["channels"][0].update(alias="front"),
        lambda p: p["lineage"]["source"].update(window="all"),
    ],
)
def test_unknown_fields_are_rejected_at_every_level(mutate):
    payload = _dump(build_scene_manifest())
    mutate(payload)
    with pytest.raises(ValidationError):
        _rebuild(payload)


def test_unsupported_schema_version_and_garbage_are_rejected():
    payload = _dump(build_scene_manifest())
    payload["schema_version"] = "sceneops.scene_manifest/v0"
    with pytest.raises(UnsupportedSceneManifestVersionError):
        load_canonical_scene_manifest(json.dumps(payload).encode())
    with pytest.raises(SceneManifestError):
        load_canonical_scene_manifest(b"\xff")
    with pytest.raises(SceneManifestError):
        load_canonical_scene_manifest(b"[]")


# --- payload, time and value primitives ----------------------------------------


def _payload(**overrides):
    fields = dict(
        artifact_id="art-payload-1",
        checksum="sha256:" + "a" * 64,
        size_bytes=1,
        media_type="image/jpeg",
    )
    fields.update(overrides)
    return PayloadRef(**fields)


def test_payload_is_referenced_by_artifact_identity_not_location():
    assert set(PayloadRef.model_fields) == {
        "artifact_id",
        "checksum",
        "size_bytes",
        "media_type",
    }
    with pytest.raises(ValidationError):
        _payload(uri="s3://b/x")
    for bad_id in ("", "s3://b/x", "samples/CAM_FRONT/x.jpg", " art-1"):
        with pytest.raises(ValidationError):
            _payload(artifact_id=bad_id)


def test_payload_references_carry_no_storage_location():
    payload = _dump(build_scene_manifest())
    for observation in payload["observations"]:
        assert set(observation["payload"]) == {
            "artifact_id",
            "checksum",
            "size_bytes",
            "media_type",
        }


@pytest.mark.parametrize(
    "media_type", ["image/JPEG", "jpeg", "image/jpeg; q=1", "image/"]
)
def test_payload_media_type_is_a_canonical_type_subtype(media_type):
    with pytest.raises(ValidationError):
        _payload(media_type=media_type)


def test_payload_requires_checksum_and_positive_size():
    with pytest.raises(ValidationError):
        _payload(checksum="md5:abc")
    with pytest.raises(ValidationError):
        _payload(size_bytes=0)


def test_timestamps_are_integer_nanoseconds():
    payload = _dump(build_scene_manifest())
    payload["observations"][0]["timestamp_ns"] = 1.5
    with pytest.raises(ValidationError):
        _rebuild(payload)
    payload["observations"][0]["timestamp_ns"] = -1
    with pytest.raises(ValidationError):
        _rebuild(payload)


def test_non_finite_numbers_and_non_rotations_are_rejected():
    payload = _dump(build_scene_manifest())
    payload["poses"][0]["transform"]["translation_m"][0] = float("nan")
    with pytest.raises(ValidationError):
        _rebuild(payload)

    payload = _dump(build_scene_manifest())
    payload["poses"][0]["transform"]["rotation_wxyz"] = [2.0, 0.0, 0.0, 0.0]
    with pytest.raises(ValidationError, match="unit quaternion"):
        _rebuild(payload)

    payload = _dump(build_scene_manifest())
    payload["annotations"][0]["box"]["size_wlh_m"][0] = -1.0
    with pytest.raises(ValidationError):
        _rebuild(payload)


# --- source fidelity -------------------------------------------------------------


def test_observations_outside_keyframes_and_their_own_timing_are_kept():
    manifest = build_scene_manifest(keyframe_timestamps_ns=(1_000, 3_000))
    keyframe_members = {i for g in manifest.groups for i in g.observation_ids}
    sweeps = [
        o for o in manifest.observations if o.observation_id not in keyframe_members
    ]
    assert [o.timestamp_ns for o in sweeps] == [2_000]

    group = manifest.keyframes()[0]
    members = {o.observation_id: o for o in manifest.observations}
    member_times = {
        members[i].channel: members[i].timestamp_ns for i in group.observation_ids
    }
    # The keyframe indexes observations; it does not re-time them.
    assert member_times == {"CAM_FRONT": 1_007, "LIDAR_TOP": 1_000}
    assert group.timestamp_ns == 1_000


def test_source_channel_identity_is_kept_verbatim():
    manifest = build_scene_manifest(
        camera_channel="/camera/front/image_raw", lidar_channel="/lidar/top/points"
    )
    assert manifest.observed_channel_names() == [
        "/camera/front/image_raw",
        "/lidar/top/points",
    ]
    payload = _dump(manifest)
    payload["channels"][0]["channel"] = " /camera/front/image_raw"
    with pytest.raises(ValidationError):
        _rebuild(payload)


def test_declared_channel_without_observations_stays_explicit():
    manifest = build_scene_manifest()
    payload = _dump(manifest)
    payload["coordinate_frames"].append({"frame_id": "RADAR_FRONT", "role": "sensor"})
    payload["coordinate_frames"].sort(key=lambda f: f["frame_id"])
    payload["channels"].append(
        {
            "channel": "RADAR_FRONT",
            "modality": "radar",
            "frame_id": "RADAR_FRONT",
            "source_clock": "nuscenes.timestamp_us",
            "sensor_id": None,
        }
    )
    payload["channels"].sort(key=lambda c: c["channel"])
    rebuilt = _rebuild(payload)
    assert "RADAR_FRONT" in [c.channel for c in rebuilt.channels]
    assert "RADAR_FRONT" not in rebuilt.observed_channel_names()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["observations"].reverse(), "canonical order"),
        (
            lambda p: p["observations"][1].update(
                observation_id=p["observations"][0]["observation_id"]
            ),
            "duplicate",
        ),
        (lambda p: p["observations"][0].update(channel="CAM_BACK"), "unknown channel"),
        (
            lambda p: p["observations"][0].update(calibration_id="cal-lidar"),
            "calibration",
        ),
        (
            lambda p: p["calibrations"][0]["extrinsic"].update(
                child_frame_id="LIDAR_TOP"
            ),
            "must place frame",
        ),
        (lambda p: p["channels"][0].update(frame_id="nowhere"), "unknown ids"),
        (lambda p: p["annotations"][0].update(group_id="keyframe-9999"), "unknown ids"),
        (lambda p: p["annotations"][0]["box"].update(frame_id="map"), "unknown ids"),
        (
            lambda p: p["groups"][0]["observation_ids"].append("zzz-missing"),
            "unknown ids",
        ),
    ],
)
def test_structural_invariants_are_enforced(mutate, message):
    payload = _dump(build_scene_manifest())
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        _rebuild(payload)


def test_observation_ego_pose_must_reference_an_ego_pose():
    payload = _dump(build_scene_manifest())
    # Re-target a pose so its child frame is a sensor frame, not the ego frame.
    pose_id = payload["observations"][0]["ego_pose_id"]
    for pose in payload["poses"]:
        if pose["pose_id"] == pose_id:
            pose["transform"]["child_frame_id"] = "CAM_FRONT"
    with pytest.raises(ValidationError, match="not an ego pose"):
        _rebuild(payload)


def test_keyframe_holds_at_most_one_observation_per_channel():
    manifest = build_scene_manifest(keyframe_timestamps_ns=(1_000, 3_000))
    payload = _dump(manifest)
    camera_ids = sorted(
        o["observation_id"]
        for o in payload["observations"]
        if o["channel"] == "CAM_FRONT"
    )
    group = payload["groups"][0]
    group["observation_ids"] = sorted(
        set(group["observation_ids"]) | set(camera_ids[:2])
    )
    with pytest.raises(ValidationError, match="more than one observation"):
        _rebuild(payload)


def test_clocks_are_declared_per_channel_and_per_unowned_structure():
    manifest = build_scene_manifest(camera_clock="camera.exposure_clock")
    clocks = {c.channel: c.source_clock for c in manifest.channels}
    assert clocks == {
        "CAM_FRONT": "camera.exposure_clock",
        "LIDAR_TOP": "mcap_log_time",
    }
    assert "source_clock" not in SceneManifest.model_fields
    # Every timestamp has exactly one reachable clock and none is converted.
    clocked = list(manifest.clocked_timestamps())
    assert len(clocked) == (
        len(manifest.observations)
        + len(manifest.poses)
        + len(manifest.groups)
        + len(manifest.annotations)
    )
    camera = [o for o in manifest.observations if o.channel == "CAM_FRONT"]
    assert ("camera.exposure_clock", camera[0].timestamp_ns) in clocked
    # A keyframe may group observations counted in different clocks.
    assert {clocks[o.channel] for o in manifest.observations} == set(clocks.values())


@pytest.mark.parametrize("structure", ["channels", "poses", "groups", "annotations"])
def test_every_timestamped_structure_must_declare_a_valid_clock(structure):
    payload = _dump(build_scene_manifest())
    del payload[structure][0]["source_clock"]
    with pytest.raises(ValidationError, match="source_clock"):
        _rebuild(payload)
    payload = _dump(build_scene_manifest())
    payload[structure][0]["source_clock"] = "Camera Clock"
    with pytest.raises(ValidationError):
        _rebuild(payload)


def test_recording_window_constrains_only_timestamps_in_the_segment_clock():
    source = recording_source(start_timestamp_ns=0, end_timestamp_ns=2_000)
    manifest = build_scene_manifest(source=source, keyframe_timestamps_ns=(500, 1_500))
    assert manifest.declared_window() == SceneTimeWindow(
        source_clock="mcap_log_time", start_timestamp_ns=0, end_timestamp_ns=2_000
    )

    with pytest.raises(ValidationError, match="outside the segment window"):
        build_scene_manifest(source=source, keyframe_timestamps_ns=(500, 1_999))

    # Camera timestamps in another clock are not comparable to the window and
    # are never converted to make them so; only mcap_log_time is checked.
    other_clock = build_scene_manifest(
        source=source,
        camera_clock="camera.exposure_clock",
        keyframe_timestamps_ns=(500, 1_999),
    )
    assert any(
        o.timestamp_ns >= 2_000
        for o in other_clock.observations
        if o.channel == "CAM_FRONT"
    )


def test_every_scene_declares_its_segment_window():
    manifest = build_scene_manifest(keyframe_timestamps_ns=(1_000, 3_000))
    window = manifest.declared_window()
    assert (
        window.source_clock,
        window.start_timestamp_ns,
        window.end_timestamp_ns,
    ) == (
        "mcap_log_time",
        0,
        10_000_000_000,
    )


def test_producer_fingerprint_must_rederive_from_the_manifest_source():
    manifest = build_scene_manifest()
    payload = _dump(manifest)
    payload["lineage"]["producer"]["build_config"] = {"channels": ["CAM_FRONT"]}
    with pytest.raises(ValidationError, match="producer_fingerprint"):
        _rebuild(payload)

    other_source = recording_source(unit_key="segment-0002")
    payload = _dump(manifest)
    payload["lineage"]["source"] = json.loads(other_source.model_dump_json())
    # Same fingerprint, different unit: still valid, because the fingerprint
    # is build-scoped (no unit key or window in the source revision).
    assert _rebuild(payload).lineage.source.unit_key == "segment-0002"

    payload["lineage"]["source"]["recording_checksum"] = "sha256:" + "2" * 64
    with pytest.raises(ValidationError, match="producer_fingerprint"):
        _rebuild(payload)


def test_canonical_provenance_rejects_a_location_or_external_block():
    payload = _dump(build_scene_manifest())
    payload["lineage"]["source"]["uri"] = "s3://sceneops/robot_runs/run-001"
    with pytest.raises(ValidationError):
        _rebuild(payload)
    payload = _dump(build_scene_manifest())
    payload["lineage"]["source"] = {
        "source_kind": "external",
        "revision": {"source_kind": "external", "format": "nuscenes"},
        "source_unit_key": "scene-0061",
    }
    with pytest.raises(ValidationError):
        _rebuild(payload)


def test_unknown_source_clock_identifier_is_rejected():
    with pytest.raises(ValidationError):
        build_scene_manifest(source_clock="NuScenes Clock")


def test_coordinate_frames_and_channels_must_be_sorted_unique():
    manifest = build_scene_manifest()
    frames = sorted(
        [*manifest.coordinate_frames, SceneCoordinateFrame(frame_id="ego", role="ego")],
        key=lambda f: f.frame_id,
    )
    with pytest.raises(ValidationError, match="duplicate"):
        SceneManifest.model_validate(
            {
                **manifest.model_dump(),
                "coordinate_frames": [f.model_dump() for f in frames],
            }
        )
    channels = list(reversed(manifest.channels))
    with pytest.raises(ValidationError, match="canonical order"):
        SceneManifest.model_validate(
            {**manifest.model_dump(), "channels": [c.model_dump() for c in channels]}
        )
    assert isinstance(manifest.channels[0], SceneChannel)


# --- identity and record projection -------------------------------------------


def test_scene_identity_is_deterministic_and_scoped():
    source = recording_source(robot_run_id="run-1", unit_key="segment-000000")
    scene_id = scene_id_for(dataset_id="d", dataset_version="v1", source=source)
    assert scene_id == scene_id_for(dataset_id="d", dataset_version="v1", source=source)
    assert scene_id.startswith("scene-") and len(scene_id) <= 128

    assert scene_id != scene_id_for(dataset_id="d", dataset_version="v2", source=source)
    assert scene_id != scene_id_for(
        dataset_id="d",
        dataset_version="v1",
        source=recording_source(robot_run_id="run-2", unit_key="segment-000000"),
    )
    # The recording revision and the window are not identity: a rebuild of
    # the same unit key keeps the id.
    rebuilt = recording_source(
        robot_run_id="run-1",
        unit_key="segment-000000",
        recording_checksum="sha256:" + "9" * 64,
        start_timestamp_ns=5,
        end_timestamp_ns=6,
        source_clock="sensor.header_stamp",
    )
    assert scene_id == scene_id_for(
        dataset_id="d", dataset_version="v1", source=rebuilt
    )
    # Pinned: the identity document is unchanged from when external units
    # existed (§29.9).
    assert (
        scene_id
        == "scene-"
        + __import__("hashlib")
        .sha256(
            __import__(
                "sceneops_core.common.canonical_json", fromlist=["x"]
            ).canonical_json_bytes(
                {
                    "unit_id_schema": "sceneops.unit_id/v1",
                    "domain": "scene",
                    "dataset_id": "d",
                    "dataset_version": "v1",
                    "source": {
                        "source_kind": "recording",
                        "robot_run_id": "run-1",
                        "unit_key": "segment-000000",
                    },
                }
            )
        )
        .hexdigest()[:32]
    )

    a = recording_source(robot_run_id="run-1", unit_key="seg-0")
    b = recording_source(robot_run_id="run-1", unit_key="seg-1")
    assert scene_id_for(dataset_id="d", dataset_version="v1", source=a) != scene_id_for(
        dataset_id="d", dataset_version="v1", source=b
    )
    # Ambiguous concatenations cannot collide.
    assert scene_id_for(
        dataset_id="d-v", dataset_version="1", source=source
    ) != scene_id_for(dataset_id="d", dataset_version="v-1", source=source)


def test_record_projection_is_derived_from_the_manifest():
    manifest = build_scene_manifest(
        keyframe_timestamps_ns=(1_000, 3_000), annotations_per_keyframe=2
    )
    record = project_scene_record(
        dataset_id="d",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id="art-1",
        manifest_checksum=manifest.checksum(),
    )
    assert record.scene_id == scene_id_for(
        dataset_id="d", dataset_version="v1", source=manifest.lineage.source
    )
    assert record.robot_run_id == "run-001"
    assert record.unit_key == "segment-0000"
    assert record.producer_fingerprint == manifest.lineage.producer.producer_fingerprint
    assert (
        record.window_clock,
        record.window_start_timestamp_ns,
        record.window_end_timestamp_ns,
    ) == ("mcap_log_time", 0, 10_000_000_000)
    assert not {"source_kind", "external_format"} & set(record.model_dump())
    assert record.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]
    assert record.observation_count == 5
    assert record.keyframe_count == 2
    assert record.annotation_count == 4
    assert record.has_ground_truth
    assert "status" not in record.model_dump()


def test_recording_record_projects_robot_run_scope():
    manifest = build_scene_manifest(
        source=recording_source(robot_run_id="run-7", unit_key="segment-0003"),
        keyframe_timestamps_ns=(1_000,),
        annotations_per_keyframe=0,
    )
    record = project_scene_record(
        dataset_id="d",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id="art-1",
        manifest_checksum=manifest.checksum(),
    )
    assert record.robot_run_id == "run-7"
    assert record.unit_key == "segment-0003"
    assert (
        record.window_clock,
        record.window_start_timestamp_ns,
        record.window_end_timestamp_ns,
    ) == ("mcap_log_time", 0, 10_000_000_000)
    assert not record.has_ground_truth


def test_producer_info_changes_change_fingerprint_not_identity():
    a = build_scene_manifest(build_config={"channels": ["CAM_FRONT", "LIDAR_TOP"]})
    b = build_scene_manifest(build_config={"channels": ["CAM_FRONT"]})
    assert (
        a.lineage.producer.producer_fingerprint
        != b.lineage.producer.producer_fingerprint
    )
    assert a.checksum() != b.checksum()
    ids = {
        scene_id_for(dataset_id="d", dataset_version="v1", source=m.lineage.source)
        for m in (a, b)
    }
    assert len(ids) == 1
    assert isinstance(a.lineage.producer, ProducerInfo)
