"""RecordingSceneBuilder (ADR-007 §29.10, §30) over real ROS 2 CDR MCAP
recordings: segmentation and the segment clock (Q4), canonical observation
time, payload extraction (Q2), calibration / pose interpretation, ordering
(I-34), provenance and fail-loud behavior."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig
from sceneops_scenes.recording_builder import (
    RECORDING_SCENE_PRODUCER_ID,
    RecordingRevision,
    RecordingSceneBuildError,
    WHOLE_RECORDING_UNIT_KEY,
    iter_planned_payloads,
    observation_payload_artifact_id,
    plan_recording_scenes,
)
from sceneops_core.scenes.recording_build import PayloadExtraction

sys.path.insert(0, str(Path(__file__).parent))
from sceneops_recording.testing.ros2_recordings import (  # noqa: E402
    CAMERA_INFO_TOPIC,
    CAMERA_TOPIC,
    JPEG,
    LIDAR_TOPIC,
    LOG_LAG_NS,
    Recording,
    build_config,
    camera_info,
    compressed_image,
    default_recording,
    tf_message,
    transform,
)

REVISION = RecordingRevision(
    robot_run_id="run-001",
    recording_checksum="sha256:" + "1" * 64,
    recording_clock="mcap_log_time",
)


def _plan(path, config=None, revision=REVISION):
    cfg = RecordingSceneBuildConfig.model_validate(config or build_config())
    return plan_recording_scenes(path, revision=revision, config=cfg)


@pytest.fixture()
def recording(tmp_path):
    return default_recording().write(tmp_path / "recording.mcap")


def _observations(plan, topic):
    return [
        o for s in plan.scenes for o in s.manifest.observations if o.channel == topic
    ]


# --- segmentation and the segment clock (Q4) -----------------------------------


def test_fixed_duration_segments_on_the_declared_clock(recording):
    plan = _plan(recording)

    assert [s.unit_key for s in plan.scenes] == ["segment-000000", "segment-000001"]
    windows = [
        (
            s.manifest.lineage.source.source_clock,
            s.manifest.lineage.source.start_timestamp_ns,
            s.manifest.lineage.source.end_timestamp_ns,
        )
        for s in plan.scenes
    ]
    # Origin = earliest included observation on the segmentation clock
    # (the first camera header stamp); windows are half-open.
    assert windows == [
        ("sensor.header_stamp", 1_000_000_000, 2_000_000_000),
        ("sensor.header_stamp", 2_000_000_000, 3_000_000_000),
    ]
    # The stamp exactly at 2.0 s belongs to the second window, not the first.
    first, second = plan.scenes
    assert max(o.timestamp_ns for o in first.manifest.observations) < 2_000_000_000
    assert min(o.timestamp_ns for o in second.manifest.observations) == 2_000_000_000


def test_segmentation_is_deterministic_and_part_of_the_fingerprint(recording):
    a, b = _plan(recording), _plan(recording)
    assert [s.manifest.to_canonical_bytes() for s in a.scenes] == [
        s.manifest.to_canonical_bytes() for s in b.scenes
    ]
    coarser = _plan(recording, build_config(duration_ns=10_000_000_000))
    assert [s.unit_key for s in coarser.scenes] == ["segment-000000"]
    assert coarser.producer.producer_fingerprint != a.producer.producer_fingerprint
    assert coarser.producer.producer_id == RECORDING_SCENE_PRODUCER_ID
    assert coarser.producer.build_config["segmentation"] == {
        "clock": "sensor.header_stamp",
        "duration_ns": 10_000_000_000,
        "policy": "fixed_duration",
    }


def whole_recording_config(*, clock: str = "sensor.header_stamp", **kwargs) -> dict:
    return {
        **build_config(clock=clock, **kwargs),
        "segmentation": {"policy": "whole_recording", "clock": clock},
    }


def test_whole_recording_is_one_scene_covering_the_recording(recording):
    plan = _plan(recording, whole_recording_config())

    (scene,) = plan.scenes
    source = scene.manifest.lineage.source
    assert scene.unit_key == WHOLE_RECORDING_UNIT_KEY == source.unit_key == "recording"
    # Half-open [earliest, latest + 1) over every observation and pose on the
    # segmentation clock: the last stamp is the lidar's, 7 ns after its frame.
    assert (
        source.source_clock,
        source.start_timestamp_ns,
        source.end_timestamp_ns,
    ) == (
        "sensor.header_stamp",
        1_000_000_000,
        2_500_000_007 + 1,
    )
    assert scene.manifest.observations[-1].timestamp_ns == 2_500_000_007
    assert plan.producer.build_config["segmentation"] == {
        "clock": "sensor.header_stamp",
        "policy": "whole_recording",
    }


def test_whole_recording_keeps_exactly_what_fixed_windows_keep(recording):
    """Segmentation only groups: the observations, poses, calibration and
    payload identities are those of the fixed-duration plan, in one Scene."""
    whole = _plan(recording, whole_recording_config())
    windows = _plan(recording)

    (scene,) = whole.scenes
    assert len(windows.scenes) == 2
    assert scene.manifest.observations == sorted(
        (o for s in windows.scenes for o in s.manifest.observations),
        key=lambda o: (o.channel, o.timestamp_ns, o.observation_id),
    )
    assert scene.manifest.poses == [p for s in windows.scenes for p in s.manifest.poses]
    assert scene.manifest.calibrations == windows.scenes[0].manifest.calibrations
    assert whole.payloads == windows.payloads
    assert whole.producer.producer_fingerprint != windows.producer.producer_fingerprint


def test_whole_recording_is_deterministic(recording):
    a = _plan(recording, whole_recording_config())
    b = _plan(recording, whole_recording_config())
    assert [s.manifest.to_canonical_bytes() for s in a.scenes] == [
        s.manifest.to_canonical_bytes() for s in b.scenes
    ]
    assert a.producer.producer_fingerprint == b.producer.producer_fingerprint


def test_whole_recording_window_starts_at_the_earliest_pose_not_only_observation(
    tmp_path,
):
    """A pose recorded before the first observation is part of the recording;
    fixed windows start at the first observation and leave it out."""
    rec = default_recording()
    rec.add(
        "/tf",
        "tf2_msgs/msg/TFMessage",
        tf_message(transform(950_000_000, "map", "base_link", (-1.0, 0.0, 0.0))),
        950_000_000 + LOG_LAG_NS,
        sequence=99,
    )
    path = rec.write(tmp_path / "early-pose.mcap")

    whole = _plan(path, whole_recording_config())
    windows = _plan(path)

    (scene,) = whole.scenes
    assert scene.manifest.lineage.source.start_timestamp_ns == 950_000_000
    assert whole.pose_count == windows.pose_count + 1


def test_whole_recording_on_the_recording_clock_keeps_source_stamps(recording):
    plan = _plan(recording, whole_recording_config(clock="mcap_log_time"))
    (scene,) = plan.scenes
    source = scene.manifest.lineage.source
    assert source.source_clock == "mcap_log_time"
    assert source.start_timestamp_ns == 1_000_000_000 + LOG_LAG_NS
    assert [o.timestamp_ns for o in _observations(plan, CAMERA_TOPIC)] == [
        1_000_000_000,
        1_500_000_000,
        2_000_000_000,
        2_500_000_000,
    ]


def test_log_time_segmentation_keeps_source_stamps_unconverted(recording):
    """Segment on the recording clock while observations keep their header
    stamps: other-clock timestamps are never compared with the window."""
    plan = _plan(recording, build_config(clock="mcap_log_time"))
    source = plan.scenes[0].manifest.lineage.source
    assert source.source_clock == "mcap_log_time"
    assert source.start_timestamp_ns == 1_000_000_000 + LOG_LAG_NS
    camera = _observations(plan, CAMERA_TOPIC)
    assert [o.timestamp_ns for o in camera] == [
        1_000_000_000,
        1_500_000_000,
        2_000_000_000,
        2_500_000_000,
    ]
    channels = {c.channel: c.source_clock for c in plan.scenes[0].manifest.channels}
    assert channels[CAMERA_TOPIC] == "sensor.header_stamp"


def test_a_channel_off_the_segment_clock_is_a_config_error():
    with pytest.raises(ValueError, match="must take its time from it"):
        RecordingSceneBuildConfig.model_validate(
            build_config(lidar_time=("log_time", "mcap_log_time"))
        )


def test_windows_never_come_from_robot_run_extent(recording):
    """Changing only the recording's declared clock fact cannot move a
    source-clock window; log_time-based builds need the recording clock."""
    plan = _plan(recording)
    with pytest.raises(RecordingSceneBuildError, match="recording clock"):
        _plan(
            recording,
            build_config(clock="mcap_log_time"),
            revision=RecordingRevision("run-001", "sha256:" + "1" * 64, "other.clock"),
        )
    assert plan.scenes[0].manifest.lineage.source.start_timestamp_ns == 1_000_000_000


# --- canonical observation time ------------------------------------------------


@pytest.mark.parametrize(
    ("time", "expected_first"),
    [
        (("header_stamp", "sensor.header_stamp"), 1_000_000_007),
        (("log_time", "mcap_log_time"), 1_000_000_007 + LOG_LAG_NS),
        (("publish_time", "mcap_publish_time"), 1_000_000_007 + LOG_LAG_NS),
    ],
)
def test_time_policy_selects_the_preserved_timing_fact(recording, time, expected_first):
    config = build_config(clock="mcap_log_time", lidar_time=time)
    plan = _plan(recording, config)
    lidar = _observations(plan, LIDAR_TOPIC)
    assert lidar[0].timestamp_ns == expected_first
    channel = next(
        c for c in plan.scenes[0].manifest.channels if c.channel == LIDAR_TOPIC
    )
    assert channel.source_clock == time[1]


def test_multi_clock_scene(recording):
    plan = _plan(
        recording,
        build_config(clock="mcap_log_time", lidar_time=("log_time", "mcap_log_time")),
    )
    clocks = {c.channel: c.source_clock for c in plan.scenes[0].manifest.channels}
    assert clocks == {CAMERA_TOPIC: "sensor.header_stamp", LIDAR_TOPIC: "mcap_log_time"}


def test_equal_timestamps_order_by_sequence_then_file_order(tmp_path):
    """I-34: canonical order is timestamp, then MCAP sequence, never
    physical position."""
    rec = default_recording(frame_stamps_ns=(1_000_000_000,))
    for seq in (3, 2):
        rec.add(
            CAMERA_TOPIC,
            "sensor_msgs/msg/CompressedImage",
            compressed_image(1_200_000_000, data=JPEG + bytes([seq])),
            1_300_000_000,
            sequence=seq,
        )
    plan = _plan(rec.write(tmp_path / "r.mcap"))
    tied = [
        o for o in _observations(plan, CAMERA_TOPIC) if o.timestamp_ns == 1_200_000_000
    ]
    payload_ids = [o.payload.artifact_id for o in tied]
    # sequence 2 (written second) sorts before sequence 3.
    assert payload_ids == [
        observation_payload_artifact_id(
            robot_run_id="run-001",
            topic=CAMERA_TOPIC,
            channel_index=index,
            extraction=PayloadExtraction.COMPRESSED_IMAGE,
        )
        for index in (2, 1)
    ]


def test_cross_channel_write_order_and_chunking_do_not_change_manifests(tmp_path):
    rec = default_recording()
    a = _plan(rec.write(tmp_path / "a.mcap"))
    # Swap messages that share a log_time across channels; chunk differently.
    order = list(range(len(rec.messages)))
    for i in range(1, len(order) - 3, 4):
        order[i], order[i + 1] = order[i + 1], order[i]
    b = _plan(rec.write(tmp_path / "b.mcap", order=order, chunk_size=256))
    assert [s.manifest.to_canonical_bytes() for s in a.scenes] == [
        s.manifest.to_canonical_bytes() for s in b.scenes
    ]


# --- payloads (Q2) ------------------------------------------------------------------


def test_camera_payload_is_the_compressed_bytes_unchanged(recording):
    plan = _plan(recording)
    payloads = dict(
        (p.artifact_id, data)
        for p, data in iter_planned_payloads(
            recording, plan, RecordingSceneBuildConfig.model_validate(build_config())
        )
    )
    camera = _observations(plan, CAMERA_TOPIC)
    for index, observation in enumerate(camera):
        assert observation.payload.media_type == "image/jpeg"
        assert payloads[observation.payload.artifact_id] == JPEG + bytes([index])
        assert observation.image_size.width_px == 1600


def test_lidar_payload_is_the_serialized_pointcloud2_message(recording):
    from sceneops_recording import iter_recording_messages

    plan = _plan(recording)
    recorded = [
        m.data for m in iter_recording_messages(recording, topics=[LIDAR_TOPIC])
    ]
    payloads = {
        p.artifact_id: data
        for p, data in iter_planned_payloads(
            recording, plan, RecordingSceneBuildConfig.model_validate(build_config())
        )
    }
    lidar = _observations(plan, LIDAR_TOPIC)
    assert [payloads[o.payload.artifact_id] for o in lidar] == recorded
    for observation, data in zip(lidar, recorded):
        assert observation.payload.media_type == (
            "application/x.ros2-cdr.sensor_msgs.msg.pointcloud2"
        )
        assert (
            observation.payload.checksum == "sha256:" + hashlib.sha256(data).hexdigest()
        )


def test_payload_ids_are_deterministic_and_independent_of_segmentation(recording):
    a = _plan(recording)
    b = _plan(recording, build_config(clock="mcap_log_time", duration_ns=10**10))
    assert set(a.payloads) == set(b.payloads)
    assert {p.artifact_id for p in a.payloads.values()} == {
        p.artifact_id for p in b.payloads.values()
    }
    other_run = _plan(
        recording,
        revision=RecordingRevision("run-002", "sha256:" + "1" * 64, "mcap_log_time"),
    )
    assert not {p.artifact_id for p in a.payloads.values()} & {
        p.artifact_id for p in other_run.payloads.values()
    }


def test_image_format_that_does_not_match_its_bytes_fails(tmp_path):
    rec = default_recording(frame_stamps_ns=(1_000_000_000,))
    rec.messages[3].message = compressed_image(1_000_000_000, data=b"\x89PNG not jpeg")
    with pytest.raises(RecordingSceneBuildError, match="not image/jpeg"):
        _plan(rec.write(tmp_path / "r.mcap"))


def test_unsupported_image_format_fails(tmp_path):
    rec = default_recording(frame_stamps_ns=(1_000_000_000,))
    rec.messages[3].message = compressed_image(1_000_000_000, fmt="rgb8")
    with pytest.raises(RecordingSceneBuildError, match="no supported media type"):
        _plan(rec.write(tmp_path / "r.mcap"))


# --- calibration and poses ---------------------------------------------------------


def test_static_calibration_and_camera_info_are_interpreted(recording):
    manifest = _plan(recording).scenes[0].manifest
    calibrations = {c.channel: c for c in manifest.calibrations}
    camera = calibrations[CAMERA_TOPIC]
    assert camera.extrinsic.parent_frame_id == "base_link"
    assert camera.extrinsic.child_frame_id == "cam_front"
    # ROS (x, y, z, w) = (-0.5, 0.5, -0.5, 0.5) -> declared [w, x, y, z].
    assert camera.extrinsic.rotation_wxyz == (0.5, -0.5, 0.5, -0.5)
    assert camera.camera_intrinsic[0] == (1266.4, 0.0, 816.3)
    assert calibrations[LIDAR_TOPIC].camera_intrinsic is None
    roles = {f.frame_id: f.role.value for f in manifest.coordinate_frames}
    assert roles == {
        "base_link": "ego",
        "map": "world",
        "cam_front": "sensor",
        "lidar_top": "sensor",
    }
    assert all(o.calibration_id for o in manifest.observations)


def test_source_poses_are_kept_without_interpolation(recording):
    plan = _plan(recording)
    poses = [p for s in plan.scenes for p in s.manifest.poses]
    assert [p.timestamp_ns for p in poses] == [
        1_000_000_000,
        1_500_000_000,
        2_000_000_000,
        2_500_000_000,
    ]
    assert {
        (p.transform.parent_frame_id, p.transform.child_frame_id) for p in poses
    } == {("map", "base_link")}
    # No observation is associated with a pose the source did not associate.
    assert all(
        o.ego_pose_id is None for s in plan.scenes for o in s.manifest.observations
    )


def test_zero_stamped_static_transforms_build_the_same_calibration(tmp_path):
    """A static transform's header stamp is not an observation time: a
    recording whose /tf_static is unstamped (stamp 0) builds the same Scenes
    as one stamped at the start, calibrations included."""
    from sceneops_core.scenes.testing import semantic_scene_content

    stamped = _plan(default_recording().write(tmp_path / "stamped.mcap"))
    rec = default_recording()
    for stamped_transform in rec.messages[0].message["transforms"]:
        stamped_transform["header"]["stamp"] = {"sec": 0, "nanosec": 0}
    zero = _plan(rec.write(tmp_path / "zero.mcap"))

    assert [semantic_scene_content(s.manifest) for s in zero.scenes] == [
        semantic_scene_content(s.manifest) for s in stamped.scenes
    ]
    assert all(c.extrinsic is not None for c in zero.scenes[0].manifest.calibrations)


def test_missing_calibration_fails_loudly(tmp_path):
    rec = default_recording()
    rec.messages[0].message = tf_message(
        transform(900_000_000, "base_link", "lidar_top")
    )
    with pytest.raises(RecordingSceneBuildError, match="no static transform"):
        _plan(rec.write(tmp_path / "r.mcap"))


def test_changing_calibration_fails_loudly(tmp_path):
    rec = default_recording()
    rec.add(
        CAMERA_INFO_TOPIC,
        "sensor_msgs/msg/CameraInfo",
        camera_info(2_600_000_000, k=[1.0] * 9),
        2_600_000_000,
    )
    with pytest.raises(RecordingSceneBuildError, match="changes within the recording"):
        _plan(rec.write(tmp_path / "r.mcap"))


def test_distortion_the_manifest_cannot_express_fails(tmp_path):
    rec = default_recording()
    for message in rec.messages:
        if message.topic == CAMERA_INFO_TOPIC:
            stamp = message.message["header"]["stamp"]
            message.message = camera_info(
                stamp["sec"] * 1_000_000_000 + stamp["nanosec"], d=(0.1, 0, 0, 0, 0)
            )
    with pytest.raises(RecordingSceneBuildError, match="cannot express"):
        _plan(rec.write(tmp_path / "r.mcap"))


def test_calibration_against_a_non_ego_frame_fails(tmp_path):
    rec = default_recording()
    rec.messages[0].message = tf_message(
        transform(900_000_000, "base_link", "cam_front"),
        transform(900_000_000, "mount", "lidar_top"),
    )
    with pytest.raises(RecordingSceneBuildError, match="not the ego frame"):
        _plan(rec.write(tmp_path / "r.mcap"))


# --- boundary and fail-loud ---------------------------------------------------------


def test_configured_topic_absent_from_recording_fails(recording):
    config = build_config()
    config["channels"][1]["topic"] = "/lidar/rear/points"
    with pytest.raises(RecordingSceneBuildError, match="not in the recording"):
        _plan(recording, config)


def test_unsupported_encoding_fails_rather_than_skipping(tmp_path):
    from mcap.writer import Writer

    path = tmp_path / "json.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start(profile="", library="test")
        schema = writer.register_schema(
            name="Points", encoding="jsonschema", data=b"{}"
        )
        channel = writer.register_channel(LIDAR_TOPIC, "json", schema)
        writer.add_message(channel, log_time=1, publish_time=1, data=b"{}")
        writer.finish()
    config = build_config(with_poses=False)
    config["channels"] = config["channels"][1:]
    config["calibration"] = {"static_transform_topics": ["/tf_static"]}
    with pytest.raises(Exception, match="only 'cdr'/'ros2msg' is supported"):
        _plan(path, config)


def test_source_provenance_is_the_recording_segment_only(recording):
    plan = _plan(recording)
    source = plan.scenes[0].manifest.lineage.source
    assert source.robot_run_id == "run-001"
    assert source.recording_checksum == REVISION.recording_checksum
    data = plan.scenes[0].manifest.to_canonical_bytes()
    for absent in (b"nuscenes", b"external", b"acquisition_origin", b"/data/"):
        assert absent not in data


def test_builder_never_branches_on_source_format():
    """I-32 guard: the builder module names no source format, origin
    metadata or acquisition mode."""
    spec = importlib.util.find_spec("sceneops_scenes.recording_builder")
    text = Path(spec.origin).read_text().lower()
    for forbidden in (
        "nuscenes",
        "acquisition_origin",
        "capture.source",
        "robot_platform",
    ):
        assert forbidden not in text


def test_empty_recording_scope_fails(tmp_path):
    rec = Recording()
    rec.add(
        "/tf_static",
        "tf2_msgs/msg/TFMessage",
        tf_message(
            transform(1, "base_link", "cam_front"),
            transform(1, "base_link", "lidar_top"),
        ),
        1,
    )
    rec.add(CAMERA_INFO_TOPIC, "sensor_msgs/msg/CameraInfo", camera_info(1), 1)
    rec.add(
        "/tf", "tf2_msgs/msg/TFMessage", tf_message(transform(1, "map", "base_link")), 1
    )
    rec.add(
        LIDAR_TOPIC,
        "sensor_msgs/msg/PointCloud2",
        __import__(
            "sceneops_recording.testing.ros2_recordings", fromlist=["x"]
        ).point_cloud(1),
        1,
    )
    config = build_config()
    with pytest.raises(RecordingSceneBuildError, match="not in the recording"):
        _plan(rec.write(tmp_path / "r.mcap"), config)
