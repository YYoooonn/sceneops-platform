"""nuScenes adapter + MCAP sink against a synthetic nuScenes dataroot:
source-record selection, message mapping, timing, ordering, determinism
and fail-loud cases. Output is read back with ``mcap_ros2``."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from synthetic_nuscenes import (
    CALIBRATION,
    CAN_IMU,
    CAN_MONITOR,
    CAN_POSE,
    INTRINSIC,
    SAMPLE_DATA,
    UNIT,
    VERSION,
    image_bytes,
    lidar_bytes,
    read_mcap,
    stamp_ns,
    write_dataroot,
)

from dataset_acquisition.events import AcquisitionError
from dataset_acquisition.mcap_sink import write_mcap
from dataset_acquisition.nuscenes import NuScenesAdapter, NuScenesSelection

UNIT_DATA = [sd for sd in SAMPLE_DATA if sd.sample in {"s1", "s2"}]
FIRST_NS = (
    min(
        [sd.timestamp for sd in UNIT_DATA]
        + [m["utime"] for m in CAN_POSE + CAN_IMU + CAN_MONITOR]
    )
    * 1000
)
LAST_NS = (
    max(
        [sd.timestamp for sd in UNIT_DATA]
        + [m["utime"] for m in CAN_POSE + CAN_IMU + CAN_MONITOR]
    )
    * 1000
)


def convert(dataroot: Path, out: Path, channels: set[str] | None = None):
    kwargs = {} if channels is None else {"channel_groups": frozenset(channels)}
    adapter = NuScenesAdapter(
        NuScenesSelection(
            dataroot=dataroot, version=VERSION, source_unit=UNIT, **kwargs
        )
    )
    summary = write_mcap(adapter.events(), out, origin=adapter.origin())
    messages, metadata, profile = read_mcap(out)
    return summary, messages, metadata, profile


@pytest.fixture()
def recording(dataroot: Path, tmp_path: Path):
    return convert(dataroot, tmp_path / "out.mcap")


def by_topic(messages, topic):
    return [m for m in messages if m.topic == topic]


def test_ros2_profile_and_standard_types(recording) -> None:
    summary, messages, _, profile = recording
    assert profile == "ros2"
    assert {(m.message_encoding, m.schema_encoding) for m in messages} == {
        ("cdr", "ros2msg")
    }
    assert {m.topic: m.schema_name for m in messages} == {
        "/camera/front/image/compressed": "sensor_msgs/msg/CompressedImage",
        "/camera/front/camera_info": "sensor_msgs/msg/CameraInfo",
        "/camera/back/image/compressed": "sensor_msgs/msg/CompressedImage",
        "/camera/back/camera_info": "sensor_msgs/msg/CameraInfo",
        "/lidar/top/points": "sensor_msgs/msg/PointCloud2",
        "/tf_static": "tf2_msgs/msg/TFMessage",
        "/tf": "tf2_msgs/msg/TFMessage",
        "/vehicle/odom": "nav_msgs/msg/Odometry",
        "/vehicle/imu": "sensor_msgs/msg/Imu",
        "/vehicle/status": "sensor_msgs/msg/BatteryState",
        "/vehicle/control": "std_msgs/msg/String",
        "/mission/status": "std_msgs/msg/String",
    }
    assert summary.message_count == len(messages)


def test_unit_selects_key_frames_and_sweeps_of_its_samples_only(recording) -> None:
    _, messages, _, _ = recording
    front = by_topic(messages, "/camera/front/image/compressed")
    lidar = by_topic(messages, "/lidar/top/points")
    # Key frames and sweeps of scene-0001; scene-0002's frame is excluded.
    assert [stamp_ns(m.decoded.header.stamp) for m in front] == [
        1_000_000_000,
        1_250_000_000,
        1_500_000_000,
    ]
    assert len(by_topic(messages, "/camera/back/image/compressed")) == 2
    assert len(lidar) == 3
    assert not any("radar" in m.topic for m in messages)


def test_camera_image_and_camera_info(recording) -> None:
    _, messages, _, _ = recording
    images = by_topic(messages, "/camera/front/image/compressed")
    infos = by_topic(messages, "/camera/front/camera_info")
    tokens = [sd.token for sd in UNIT_DATA if sd.channel == "CAM_FRONT"]
    for image, info, token in zip(images, infos, tokens, strict=True):
        assert image.decoded.format == "jpeg"
        assert bytes(image.decoded.data) == image_bytes(token)  # passed through
        assert image.decoded.header.frame_id == "cam_front"
        assert (stamp_ns(info.decoded.header.stamp), info.decoded.header.frame_id) == (
            stamp_ns(image.decoded.header.stamp),
            image.decoded.header.frame_id,
        )
        assert (info.decoded.height, info.decoded.width) == (900, 1600)
        assert info.decoded.distortion_model == "plumb_bob"
        assert list(info.decoded.d) == [0.0] * 5
        assert list(info.decoded.k) == [v for row in INTRINSIC for v in row]
        assert list(info.decoded.r) == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        assert list(info.decoded.p) == [
            *INTRINSIC[0],
            0.0,
            *INTRINSIC[1],
            0.0,
            *INTRINSIC[2],
            0.0,
        ]


def test_lidar_point_cloud_carries_source_bytes(recording) -> None:
    _, messages, _, _ = recording
    clouds = by_topic(messages, "/lidar/top/points")
    tokens = [sd.token for sd in UNIT_DATA if sd.channel == "LIDAR_TOP"]
    for cloud, token in zip(clouds, tokens, strict=True):
        msg = cloud.decoded
        raw = lidar_bytes(token)
        assert bytes(msg.data) == raw
        assert msg.header.frame_id == "lidar_top"
        assert [(f.name, f.offset, f.datatype, f.count) for f in msg.fields] == [
            ("x", 0, 7, 1),
            ("y", 4, 7, 1),
            ("z", 8, 7, 1),
            ("intensity", 12, 7, 1),
            ("ring", 16, 7, 1),
        ]
        assert (msg.height, msg.width, msg.point_step, msg.row_step) == (
            1,
            len(raw) // 20,
            20,
            len(raw),
        )
        assert msg.is_bigendian is False
        points = np.frombuffer(bytes(msg.data), dtype="<f4").reshape(-1, 5)
        assert points[1].tolist() == [0.5, -0.25, 2.0, 1.0, 1.0]


def test_tf_static_precedes_every_observation(recording) -> None:
    _, messages, _, _ = recording
    static = by_topic(messages, "/tf_static")
    assert len(static) == 1
    assert messages.index(static[0]) <= 1  # only "mission running" may share its slot
    transforms = {t.child_frame_id: t for t in static[0].decoded.transforms}
    assert set(transforms) == {"cam_front", "cam_back", "lidar_top"}
    for child, channel in (
        ("cam_front", "CAM_FRONT"),
        ("cam_back", "CAM_BACK"),
        ("lidar_top", "LIDAR_TOP"),
    ):
        t = transforms[child]
        translation, (w, x, y, z) = CALIBRATION[channel]
        assert t.header.frame_id == "base_link"
        assert stamp_ns(t.header.stamp) == FIRST_NS == static[0].log_time
        tr, rot = t.transform.translation, t.transform.rotation
        assert [tr.x, tr.y, tr.z] == translation
        assert [rot.x, rot.y, rot.z, rot.w] == [x, y, z, w]


def test_ego_pose_on_tf_for_every_unit_sample_data(recording) -> None:
    _, messages, _, _ = recording
    poses = by_topic(messages, "/tf")
    # Radar's ego pose is the vehicle's localization too, even though radar
    # itself is not converted.
    assert len(poses) == len(UNIT_DATA)
    for msg, sd in zip(
        poses, sorted(UNIT_DATA, key=lambda s: (s.timestamp, s.token)), strict=True
    ):
        (t,) = msg.decoded.transforms
        assert (t.header.frame_id, t.child_frame_id) == ("map", "base_link")
        assert stamp_ns(t.header.stamp) == sd.timestamp * 1000 == msg.log_time
        assert t.transform.translation.x == pytest.approx(100.0 + sd.timestamp / 1e6)
        assert (t.transform.rotation.x, t.transform.rotation.w) == (0.0, 0.96)


def test_can_telemetry(recording) -> None:
    _, messages, _, _ = recording
    odom = by_topic(messages, "/vehicle/odom")
    imu = by_topic(messages, "/vehicle/imu")
    status = by_topic(messages, "/vehicle/status")
    control = by_topic(messages, "/vehicle/control")
    assert [stamp_ns(m.decoded.header.stamp) for m in odom] == [
        p["utime"] * 1000 for p in CAN_POSE
    ]
    assert [stamp_ns(m.decoded.header.stamp) for m in imu] == [
        p["utime"] * 1000 for p in CAN_IMU
    ]
    o = odom[1].decoded
    assert o.header.frame_id == "odom"
    assert [o.pose.pose.position.x, o.twist.twist.linear.x] == [11.0, 6.0]
    assert [o.pose.pose.orientation.w, o.pose.pose.orientation.z] == [0.9, 0.436]
    assert imu[0].decoded.header.frame_id == "imu"
    assert status[1].decoded.percentage == pytest.approx(0.90)
    assert status[1].decoded.present is True
    assert json.loads(control[1].decoded.data) == {
        "steering": 4.0,
        "throttle": 10,
        "brake": 0,
        "source_timestamp_ns": 1_400_000_000,
    }


def test_mission_events_sit_on_the_source_timeline(recording) -> None:
    _, messages, _, _ = recording
    running, completed = by_topic(messages, "/mission/status")
    assert messages[0] in (running, by_topic(messages, "/tf_static")[0])
    assert messages[-1] == completed
    assert json.loads(running.decoded.data) == {
        "mission_id": f"mission-{UNIT}",
        "operation_state": "running",
        "source_timestamp_ns": FIRST_NS,
    }
    assert json.loads(completed.decoded.data)["source_timestamp_ns"] == LAST_NS
    assert (running.log_time, completed.log_time) == (FIRST_NS, LAST_NS)


def test_timing_receive_time_equals_source_time(recording) -> None:
    summary, messages, _, _ = recording
    for m in messages:
        assert m.publish_time == m.log_time
        header = getattr(m.decoded, "header", None)
        if header is not None:
            assert stamp_ns(header.stamp) == m.log_time
        if m.schema_name == "std_msgs/msg/String":
            assert json.loads(m.decoded.data)["source_timestamp_ns"] == m.log_time
    log_times = [m.log_time for m in messages]
    assert log_times == sorted(log_times)
    assert (summary.first_log_time_ns, summary.last_log_time_ns) == (FIRST_NS, LAST_NS)


def test_order_and_sequences_are_deterministic(recording) -> None:
    _, messages, _, _ = recording
    per_topic: dict[str, list[int]] = {}
    for m in messages:
        per_topic.setdefault(m.topic, []).append(m.sequence)
    assert all(seq == list(range(1, len(seq) + 1)) for seq in per_topic.values())
    # CAM_BACK and CAM_FRONT share t=1.0 s: ties break by topic.
    at_one_second = [m.topic for m in messages if m.log_time == 1_000_000_000]
    assert at_one_second == sorted(at_one_second)
    assert "/camera/back/image/compressed" in at_one_second


def test_same_input_gives_identical_recording(dataroot: Path, tmp_path: Path) -> None:
    a, *_ = convert(dataroot, tmp_path / "a.mcap")
    b, *_ = convert(dataroot, tmp_path / "b.mcap")
    assert a.sha256 == b.sha256
    assert (tmp_path / "a.mcap").read_bytes() == (tmp_path / "b.mcap").read_bytes()


def test_acquisition_origin_is_the_only_metadata(recording) -> None:
    _, messages, metadata, _ = recording
    assert metadata == {
        "sceneops.acquisition_origin": {
            "tool": "sceneops-dataset-acquisition",
            "tool_version": "0.1.0",
            "source_format": "nuscenes",
            "source_version": VERSION,
            "source_unit": UNIT,
            "channel_groups": "camera,lidar,pose,can,mission",
        }
    }
    # No source tokens (sample, sample_data, scene) leak into payloads.
    for m in messages:
        for token in ("scene-a", "sd-cf-1", 's1"', "sample_token", "is_key_frame"):
            assert token.encode() not in m.data


def test_channel_group_selection(dataroot: Path, tmp_path: Path) -> None:
    _, messages, _, _ = convert(dataroot, tmp_path / "can.mcap", {"can"})
    assert {m.topic for m in messages} == {
        "/vehicle/odom",
        "/vehicle/imu",
        "/vehicle/status",
        "/vehicle/control",
    }
    _, messages, metadata, _ = convert(dataroot, tmp_path / "cam.mcap", {"camera"})
    static = by_topic(messages, "/tf_static")[0]
    assert {t.child_frame_id for t in static.decoded.transforms} == {
        "cam_front",
        "cam_back",
    }
    assert metadata["sceneops.acquisition_origin"]["channel_groups"] == "camera"
    assert not by_topic(messages, "/lidar/top/points")


def test_calibration_change_within_unit_fails(tmp_path: Path) -> None:
    root = write_dataroot(tmp_path / "ns", extra_calibration=True)
    with pytest.raises(AcquisitionError, match="CAM_FRONT has 2 calibrations"):
        convert(root, tmp_path / "out.mcap")
    assert not (tmp_path / "out.mcap").exists()
    assert not (tmp_path / "out.mcap.partial").exists()


def test_missing_can_bus_fails_loudly(tmp_path: Path) -> None:
    root = write_dataroot(tmp_path / "ns", can=False)
    with pytest.raises(AcquisitionError, match="CAN bus 'pose' unavailable"):
        convert(root, tmp_path / "out.mcap")
    _, messages, _, _ = convert(
        root, tmp_path / "ok.mcap", {"camera", "lidar", "pose", "mission"}
    )
    assert not any(m.topic.startswith("/vehicle/") for m in messages)


def test_selection_errors(dataroot: Path) -> None:
    with pytest.raises(AcquisitionError, match="not found"):
        NuScenesAdapter(
            NuScenesSelection(
                dataroot=dataroot, version=VERSION, source_unit="scene-9999"
            )
        )
    with pytest.raises(AcquisitionError, match="channel groups"):
        NuScenesSelection(
            dataroot=dataroot,
            version=VERSION,
            source_unit=UNIT,
            channel_groups=frozenset({"radar"}),
        )
